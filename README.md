# AI 增强型知识社区

一个基于 **Flask + MySQL** 的网页版论坛/知识社区系统，内置 RAG 智能问答与 SocketIO 实时推送能力。

## 功能概览

- 用户注册 / 登录，密码 PBKDF2-SHA256 加密存储
- 板块、帖子（公开/私密、本名/匿名）、评论（楼层）
- 点赞、收藏、通知（点赞/评论/置顶/加精/删帖）
- 管理员后台：用户管理、内容管理
- RAG 智能问答：本地 TF-IDF 检索 + 可选第三方 LLM 生成回答
- SocketIO 实时推送：新帖/新评论/删除内容同步，无需手动刷新

## 技术栈

- Python 3
- Flask + Flask-SocketIO（Web 服务与实时推送）
- pyodbc（连接 MySQL）
- MySQL 8+（InnoDB，utf8mb4）

## 环境准备

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> 建议使用虚拟环境：`python -m venv venv`，然后在 Windows 下用 `venv\Scripts\python.exe` 运行。

### 2. 安装 MySQL ODBC 驱动

`config.py` 默认使用 `MySQL ODBC 9.7 Unicode Driver`。若未安装，请到 MySQL 官网下载
[MySQL Connector/ODBC](https://dev.mysql.com/downloads/connector/odbc/)，或把
`config.py` 中的 `driver` 改成你本机已安装的 ODBC 驱动名。

### 3. 建库建表

新建数据库 `ai_forum`（字符集 utf8mb4），然后执行完整建表脚本：

```bash
mysql -u root -p < database/create_tables.sql
```

`database/create_tables.sql` 是**最新完整版**，包含所有字段与表，新环境只需执行这一份。

> 若你之前已按旧版增量脚本建过库，只需再执行 `database/migrations/migrate_notification.sql`
> 来修复通知表（管理员删除帖子后通知无法送达的问题）。

### 4. 配置

复制 `backend/config/config.example.py` 为 `backend/config/config.py`，填入你自己的数据库密码与（可选的）AI API Key：

```bash
copy backend\config\config.example.py backend\config\config.py
```

- `DB_CONFIG.password`：你的 MySQL root 密码
- `AI_CONFIG.api_key`：硅基流动等 OpenAI 兼容接口的 Key，**不填则自动降级为本地检索拼接回答**

> `config.py` 已在 `.gitignore` 中，不会被提交。**切勿把真实密码/Key 提交进 Git。**

### 5. 启动

在项目根目录下运行：

```bash
venv\Scripts\python.exe web\app.py
```

然后浏览器访问 `http://127.0.0.1:5000`

## 目录结构

```
ai_forum/
├── database/                  # 数据库板块
│   ├── create_tables.sql      # 最新完整建表脚本
│   └── migrations/            # 增量迁移脚本
├── backend/                   # 后端板块（Web端业务逻辑）
│   ├── config/                # 配置（数据库连接 + AI接口）
│   ├── core/                  # 核心基础设施（数据库连接工具）
│   ├── services/              # 业务服务层（按功能模块划分）
│   │   ├── auth.py            #   登录注册
│   │   ├── user_service.py    #   用户管理
│   │   ├── board_service.py   #   板块管理
│   │   ├── post_service.py    #   发帖/帖子
│   │   ├── comment_service.py #   评论
│   │   ├── like_service.py    #   点赞
│   │   ├── favorite_service.py#   收藏
│   │   ├── notification_service.py # 通知
│   │   ├── admin_service.py   #   管理员
│   │   └── rag_service.py     #   AI问答/RAG
│   ├── avatars/               # 用户头像文件
│   └── post_images/           # 帖子图片文件
├── web/                       # Web端前端（Flask）
│   ├── app.py                 # Flask主应用
│   ├── static/                # 静态资源
│   └── templates/             # Jinja2模板
├── requirements.txt
└── README.md
```

## 说明

- 私密帖子的正文不会被发送到第三方 AI，也不会进入 AI 问答检索库。
- 匿名帖在数据层即不暴露真实 `author_id`。
