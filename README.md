# 同行评审系统（Peer Review）

一个双角色的会议/期刊评审管理系统：

- **管理员（admin）**：创建评审轮次（每轮至多 **20 份稿件**、**10 名评审员**），
  手动维护稿件、评审员与稿件之间的**利益冲突（COI）**、手动分配稿件，并在所有评审
  最终提交后**原子冻结**整轮、向评审员揭示作者身份。
- **评审员（reviewer）**：只能看到本轮的**匿名稿号**与**自己的分配**；
  可按**修订号**保存草稿（乐观并发版本号），确认后**最终提交**；冻结后内容只读，
  作者身份才会出现。

技术栈：**React 18 + Vite**（nginx 静态托管并反代 API）、
**FastAPI + SQLAlchemy 2 + JWT（bcrypt）**、**PostgreSQL 16**，
一键验收使用 **pytest + httpx** 的 `verify` 服务。

## 快速开始

```bash
docker compose up -d --build
# 前端：http://localhost:8080
# API 健康检查：http://localhost:8000/health（容器网络内）
```

默认账号（首次启动自动播种，密码可通过环境变量覆盖）：

| 角色 | 用户名 | 密码 |
| --- | --- | --- |
| 管理员 | `admin` | `admin123` |
| 评审员 | `r01` … `r12` | `reviewer123` |

系统共播种 12 名评审员，单轮最多选用其中 10 名。

## 固定验收流程

依次执行以下三条命令（与 CI 完全一致）：

```bash
docker compose config --quiet
docker compose build
docker compose run --rm verify
```

`verify` 是一次性服务：它等待 `api` 健康后，通过容器网络对真实运行的 API
（PostgreSQL 后端）执行 `tests/` 下的全部 pytest 用例，退出码非 0 即验收失败。

测试覆盖：

- **两种角色的越权请求**：评审员访问全部管理接口 403、管理员访问评审接口 403、
  未认证 401、猜测他人的 assignment/round id 一律 404（不通过 403 泄露存在性）、
  登录失败响应不可枚举账号。
- **业务规则**：>20 稿件、>10 评审员、每人 >3 份、命中利益冲突均不可提交分配；
  已存在分配时不能补登记冲突；有评审最终提交后分配锁定；未全部提交不能冻结；
  冻结后一切写操作 409。
- **并发修订**：草稿按 `(assignment, revision_no)` 独立保存，乐观版本号做
  原子 compare-and-swap（5 个并发同版本写入恰有 1 个成功）；最终提交与冻结
  在多线程下并发，断言无论如何交错，最终只可能形成「全部提交 → 冻结成功」或
  「冻结先行被拒 → 顺序重试后冻结成功」两种**合法**前后顺序，且内容一致、
  冻结后不可篡改。
- **揭示前后的响应字段**：冻结前评审员的列表/详情 JSON 中递归不出现任何
  `author*` 键与作者姓名字符串；错误响应（404/401/403）同样不含作者信息；
  所有 API 响应（含错误页）带 `Cache-Control: no-store`，前端 `index.html`
  也不缓存；冻结后同一接口才出现 `author_name`。

## 关键设计

### 匿名性（不泄露作者身份）

- 评审员端使用与管理员**物理隔离的 Pydantic 输出模型**；冻结前轮次稿件列表只返回
  `{manuscript_no, revision_no}`，分配详情不序列化 `author_name` 键（连 `null`
  都不出现，避免客户端/缓存得知该字段存在）。
- 归属权检查：评审员只能访问 `reviewer_id == 自己` 的 assignment；**属主不符与
  资源不存在返回完全相同的 404 报文**。非本轮成员访问轮次同样返回 404。
- 统一异常只回 `{"detail": ...}`，不回堆栈；登录失败对「用户不存在」和「密码错误」
  返回完全相同的响应。
- 中间件给**每个** API 响应（包括 4xx/5xx）加
  `Cache-Control: no-store, no-cache, must-revalidate`；nginx 对 `index.html`
  同样 no-store，内容哈希的 `/assets/*` 才允许长缓存。

### 修订与草稿

- 稿件有整数 `revision_no`，管理员通过 `bump-revision` 记录新修订到达。
- 草稿表按 `(assignment_id, revision_no)` 唯一：评审员可对**任意修订号**留存草稿；
  每份草稿有单调递增的 `version`，保存时带 `expected_version` 做
  `UPDATE ... WHERE version=?` 的**原子 CAS**（PostgreSQL/SQLite 行为一致）。
- **最终提交**必须针对稿件的当前修订号且必须有评分；提交后 `assignment.submitted`
  置位，草稿与提交均锁定。

### 冻结的原子性与并发顺序

所有变更操作都遵循统一的加锁顺序——**先锁 `rounds` 行（`SELECT … FOR UPDATE`），
再锁 `assignments`/子资源行**，避免死锁：

- 评审员最终提交：锁轮次行 → 检查未冻结、本人未提交 → 写入草稿并置提交位 → 提交事务。
- 管理员冻结：锁轮次行 → 重新读取全部 assignment，校验「未冻结、至少 1 条分配、
  全部已提交、无冲突、无超载」→ 同一事务内置 `is_frozen` 与 `frozen_at`。

因此最终提交与冻结并发时，二者在行锁上线性化，只可能产生合法的前后顺序，
不可能出现「冻结后还有提交成功」或「部分提交被冻结」。冻结后所有管理端与评审端
写路径都在锁内第一时间返回 409。

## API 概览

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | 公开 | OAuth2 表单登录，返回 JWT |
| GET | `/api/auth/me` | 任意 | 当前身份 |
| GET/POST | `/api/admin/rounds` | 管理员 | 轮次列表/创建 |
| GET | `/api/admin/rounds/{id}` | 管理员 | 轮次详情（稿件/冲突/分配/进度） |
| POST/PATCH | `.../manuscripts` | 管理员 | 新增稿件（≤20）/改稿 |
| POST | `.../manuscripts/{no}/bump-revision` | 管理员 | 修订号 +1 |
| POST/DELETE | `.../conflicts` | 管理员 | 维护利益冲突 |
| PUT | `.../assignments` | 管理员 | 原子替换分配矩阵（校验 10/3/冲突） |
| POST | `.../freeze` | 管理员 | 全部提交后原子冻结并揭示 |
| GET | `/api/reviewer/rounds[/{id}]` | 评审员 | 我的轮次/本轮匿名稿号+我的分配 |
| GET | `/api/reviewer/assignments/{id}` | 评审员 | 我的分配详情（含各修订草稿） |
| PUT | `/api/reviewer/assignments/{id}/draft` | 评审员 | 按修订号存草稿（CAS 版本号） |
| POST | `/api/reviewer/assignments/{id}/submit` | 评审员 | 最终提交（当前修订、必填评分） |

## 本地开发（不用 Docker）

后端：

```bash
cd backend
pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg://review:review@localhost:5432/review"
uvicorn app.main:app --reload
```

前端：

```bash
cd web
npm install
npm run dev   # 自带 /api -> localhost:8000 代理
```

后端对 SQLite 也可运行（设置 `DATABASE_URL=sqlite:///./dev.db`），方便离线快速验证；
正式验收以 compose 中的 PostgreSQL 为准。
