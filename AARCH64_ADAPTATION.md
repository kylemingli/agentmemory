# agentmemory aarch64 适配说明

> 本文档说明 agentmemory 在 Termux/aarch64 环境上的适配改动。

## 背景

agentmemory 默认的本地向量方案(transformers.js + onnxruntime-node)在 Termux/Android 上无法工作:

- `onnxruntime-node` 只有 glibc 编译版,Android 的 bionic 无法加载
- Python 的 torch/onnxruntime 也没有 Android 轮子
- 完整 Linux 环境(proot)体积太大,不可接受

因此改用 MNN 引擎(阿里移动端推理引擎,bionic 原生)+ iii-android(本地编译的 Android 原生引擎)。

## 改动概览

### 1. MNN 向量 provider

**新增文件**:`src/providers/embedding/mnn.ts`

- 实现 `EmbeddingProvider` 接口(name="mnn",dimensions=1024)
- 通过 N-API 插件 `mnn_embedding.node` 调用 libMNN.so
- 模型:bge-large-zh-MNN(1024 维,中文优化)
- 启用:`EMBEDDING_PROVIDER=mnn` + `MNN_EMBEDDING_MODEL_PATH`

**修改文件**:`src/providers/embedding/index.ts`

- 注册 "mnn" case
- 新增 `MNNEmbeddingProvider` 导出

**二进制依赖**:

- `mnn_embedding.node`:N-API 插件(根目录,编译产物)
- `libMNN.so`:MNN 推理引擎(~/.local/lib/,需单独部署)

### 2. hook 端点转 MCP 工具

**修改文件**:`src/mcp/tools-registry.ts`(+6 个工具定义)
**修改文件**:`src/mcp/server.ts`(+6 个 case 映射)

hook 脚本原本直接 POST 的 6 个 REST 端点,重新暴露为 MCP 工具,使纯 MCP 客户端(LineCodePro)无需宿主 hook 系统也能调用:

| MCP 工具 | 对应 REST 端点 | 作用 |
|---------|---------------|------|
| memory_observe | /agentmemory/observe | 写入观察 |
| memory_session_start | /agentmemory/session/start | 会话开始 |
| memory_session_end | /agentmemory/session/end | 会话结束 |
| memory_session_commit | /agentmemory/session/commit | 关联提交 |
| memory_enrich | /agentmemory/enrich | 上下文注入 |
| memory_context | /agentmemory/context | 获取上下文 |

映射方式:

- `mem::*` 函数(observe/enrich/context)→ 直接触发(payload 直传)
- `api::*` 函数(session_start/end/commit)→ 包装触发(payload 包成 `{ body: {...} }`,因为 ApiRequest 解构 req.body)

### 3. 引擎替换

agentmemory 默认用 glibc 版 iii 引擎(v0.11.2),在 Termux 上需要 glibc-runner 包装。替换为本地编译的 iii-android(v0.23.0-rc.4,bionic 原生):

- `~/.agentmemory/bin/iii.real` ← iii-android 二进制
- `~/.agentmemory/bin/iii` ← 直接执行(非 glibc-runner 包装)
- `.env` 配 `AGENTMEMORY_III_VERSION=0.23.0-rc.4`(绕过 CLI 版本检查)

### 4. 检索权重调优

针对 bge-large-zh 模型调优混合检索权重:

```
BM25_WEIGHT=0.2
VECTOR_WEIGHT=0.8
```

默认(0.4/0.6)下 BM25 的关键词巧合会压制向量语义分。

## 部署

**Termux 特化部署脚本**:`deploy-termux-aarch64.sh`

```bash
./deploy-termux-aarch64.sh
```

自动完成:

1. 下载 libMNN.so(网盘直链)
2. 下载 iii-android(网盘直链 tar.gz)
3. 下载 bge-large-zh 模型(HF)
4. 配置 .env
5. 验证并启动

**手动部署**:见同目录笔记或部署脚本注释。

## 模型说明

MNN 生态的 embedding 模型仅 5 个:

| 模型 | 状态 |
|------|------|
| bge-large-zh-MNN | ✅ 选用(1024 维,区分度最好) |
| gte-multilingual-MNN | ⚠️ 可用但区分度差 |
| Qwen3-Embedding-0.6B-MNN | ❌ MNN 崩溃 |
| Qwen3-Embedding-4B/8B | ❌ 太大 |

模型从 HF 下载(taobao-mnn),注意 `embeddings_bf16.bin` 需软链为 `embedding.mnn.weight`,否则权重缺失导致向量失效。

## 平台边界

- **Termux/aarch64**:使用本适配(MNN + iii-android)
- **完整 Linux(开发板/路由器等)**:无需适配,默认 onnxruntime + glibc 引擎即可
- **x86_64**:无需适配,默认方案

## 文件清单

| 文件 | 改动 |
|------|------|
| `src/providers/embedding/mnn.ts` | 新增 |
| `src/providers/embedding/index.ts` | +4 行 |
| `src/mcp/tools-registry.ts` | +101 行 + 注释 |
| `src/mcp/server.ts` | +278 行 + 注释 |
| `mnn_embedding.node` | 新增二进制 |
| `agentmemory_ctl.py` | 新增(运维脚本) |
| `deploy-termux-aarch64.sh` | 新增(部署脚本) |

## MCP 端点

LineCodePro 等纯 HTTP MCP 客户端连接地址:

```
http://127.0.0.1:3111/agentmemory/jsonrpc
```

请求格式(简化 JSON-RPC,非标准 MCP initialize 握手):

- 列工具:`{"method":"tools/list"}`
- 调工具:`{"method":"tools/call","params":{"name":"memory_recall","arguments":{"query":"..."}}}`

注意:

- 根路径 `http://127.0.0.1:3111` 和 `/agentmemory/health` 不是 MCP 端点,POST 会 405
- `/agentmemory/mcp/tools` 仅 GET,POST 会 405
- 标准 MCP `initialize` 握手不受支持,调用方必须直接发 `tools/list` / `tools/call`
