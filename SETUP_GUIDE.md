# 安装与首次运行

项目选题：基于 RAG 技术的生物领域知识库系统研发。

## 环境

Python 3.10+，Windows 或 Linux。本地离线模式无需 GPU；模型资源需求取决于实际部署的 embedding 与生成模型。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Linux 对应解释器路径为 `.venv/bin/python`。只有首次没有配置文件时，将 `.env.example` 复制为 `.env`。已有 `.env` 中旧项目的 SCI_ / OLLAMA_ 配置不会自动被新主线采用，应按 BIOKB_ 字段明确迁移。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m biokb doctor
.\.venv\Scripts\python.exe -m biokb import-legacy
.\.venv\Scripts\python.exe app.py
```

浏览器访问 `http://127.0.0.1:5000`。迁移命令适用于保留了原项目语料的工作目录；分发源码包不包含私人文献，新环境请通过文献管理上传文件。

## 模型启用

先验证离线文献与检索流程。随后在 `.env` 分别设置向量和语言模型后端、服务地址、实际模型名和凭据，重启服务。更换向量模型后执行 `python -m biokb reindex`。云端模型会接收相应文本，启用前按资料使用要求选择服务。

详细步骤见 [部署文档](docs/部署文档.md)，配置字段见 [.env.example](.env.example)。完整工作区中的旧科研配图安装说明保存在 `docs/archive/illustration/SETUP_GUIDE.md`；分发源码包只包含新主线，不携带历史归档和原始文献。
