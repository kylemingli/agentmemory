#!/bin/sh
# agentmemory Termux aarch64 部署脚本
# 解决:MNN 向量依赖 + iii-android 引擎 + 配置 + 启动
# 仅适用于 Termux 环境(Android 用户态,无 glibc)

set -e

HOME_DIR="$HOME"
AGENTMEMORY_DIR="$HOME_DIR/git/agentmemory"
LIBMNN_URL="https://links.8uid.com/d/6802662945e2cebdc7b268ecbb51fc9a"
III_ANDROID_URL="https://links.8uid.com/d/c587e1070aebca62fc6c1f80a1582367"
MODEL_BASE="https://huggingface.co/taobao-mnn/bge-large-zh-MNN/resolve/main"

echo "== agentmemory Termux aarch64 部署 =="
echo ""

# ============ 1. 环境检查 ============
echo "[1/7] 环境检查"

if [ ! -d "$AGENTMEMORY_DIR" ]; then
  echo "错误:agentmemory 目录不存在:$AGENTMEMORY_DIR"
  echo "请先 clone:git clone https://github.com/rohitg00/agentmemory ~/git/agentmemory"
  exit 1
fi

command -v node >/dev/null 2>&1 || { echo "错误:node 未安装"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "错误:python3 未安装"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "错误:curl 未安装"; exit 1; }

echo "  环境正常"
echo ""

# ============ 2. libMNN.so ============
echo "[2/7] 获取 libMNN.so"

mkdir -p "$HOME_DIR/.local/lib"

if [ -f "$HOME_DIR/.local/lib/libMNN.so" ]; then
  echo "  libMNN.so 已存在,跳过下载"
else
  echo "  从直链下载 libMNN.so..."
  curl -L --max-time 120 -o "$HOME_DIR/.local/lib/libMNN.so" "$LIBMNN_URL"
fi

# 复制到系统库路径(Node 加载需要)
SYS_LIB="/data/data/com.termux/files/usr/lib"
if [ -d "$SYS_LIB" ]; then
  cp "$HOME_DIR/.local/lib/libMNN.so" "$SYS_LIB/"
  echo "  libMNN.so 已复制到 $SYS_LIB"
fi

echo ""

# ============ 2b. iii-android 引擎 ============
echo "[2b/7] 获取 iii-android 引擎"

ENGINE_DIR="$HOME_DIR/.agentmemory/bin"
mkdir -p "$ENGINE_DIR"

if [ -f "$ENGINE_DIR/iii.real" ] && "$ENGINE_DIR/iii.real" --version 2>/dev/null | grep -q "0.23.0"; then
  echo "  iii-android 已部署,跳过"
else
  echo "  从直链下载 iii-android.tar.gz..."
  curl -L --max-time 120 -o /tmp/iii-android.tar.gz "$III_ANDROID_URL"
  tar -xzf /tmp/iii-android.tar.gz -C /tmp/
  cp /tmp/iii-android "$ENGINE_DIR/iii.real"
  chmod +x "$ENGINE_DIR/iii.real"
  rm -f /tmp/iii-android.tar.gz /tmp/iii-android
  echo "  iii-android 已部署到 $ENGINE_DIR/iii.real"
fi

# 重写 iii 包装脚本为直接执行
cat > "$ENGINE_DIR/iii" << 'SH'
#!/data/data/com.termux/files/usr/bin/sh
exec "$HOME/.agentmemory/bin/iii.real" "$@"
SH
chmod +x "$ENGINE_DIR/iii"
echo "  iii 包装脚本已更新"

echo ""

# ============ 3. MNN 模型 ============
echo "[4/7] 下载 bge-large-zh 模型"

MODEL_DIR="$HOME_DIR/mnn/bge-large-zh"
mkdir -p "$MODEL_DIR"

if [ -f "$MODEL_DIR/embedding.mnn" ] && [ -f "$MODEL_DIR/embeddings_bf16.bin" ]; then
  echo "  模型已存在,跳过下载"
else
  echo "  下载 embedding.mnn (173MB)..."
  curl -L --max-time 600 -o "$MODEL_DIR/embedding.mnn" "$MODEL_BASE/embedding.mnn"

  echo "  下载 embeddings_bf16.bin (43MB)..."
  curl -L --max-time 300 -o "$MODEL_DIR/embeddings_bf16.bin" "$MODEL_BASE/embeddings_bf16.bin"

  echo "  下载 tokenizer 和配置..."
  curl -L --max-time 120 -o "$MODEL_DIR/tokenizer.txt" "$MODEL_BASE/tokenizer.txt"
  curl -L --max-time 60 -o "$MODEL_DIR/llm_config.json" "$MODEL_BASE/llm_config.json"
  curl -L --max-time 60 -o "$MODEL_DIR/config.json" "$MODEL_BASE/config.json"
fi

# 权重软链(关键:MNN 期望 embedding.mnn.weight 文件名)
ln -sf embeddings_bf16.bin "$MODEL_DIR/embedding.mnn.weight"

echo ""

# ============ 4. 配置 ============
echo "[5/7] 配置 .env"

ENV_FILE="$HOME_DIR/.agentmemory/.env"
mkdir -p "$HOME_DIR/.agentmemory"

# 只追加不覆盖已有配置
grep -q "^EMBEDDING_PROVIDER=" "$ENV_FILE" 2>/dev/null || echo "EMBEDDING_PROVIDER=mnn" >> "$ENV_FILE"
grep -q "^MNN_EMBEDDING_MODEL_PATH=" "$ENV_FILE" 2>/dev/null || echo "MNN_EMBEDDING_MODEL_PATH=$MODEL_DIR/embedding.mnn" >> "$ENV_FILE"
grep -q "^BM25_WEIGHT=" "$ENV_FILE" 2>/dev/null || echo "BM25_WEIGHT=0.2" >> "$ENV_FILE"
grep -q "^VECTOR_WEIGHT=" "$ENV_FILE" 2>/dev/null || echo "VECTOR_WEIGHT=0.8" >> "$ENV_FILE"
grep -q "^AGENTMEMORY_III_VERSION=" "$ENV_FILE" 2>/dev/null || echo "AGENTMEMORY_III_VERSION=0.23.0-rc.4" >> "$ENV_FILE"

echo "  配置完成:"
echo "  - EMBEDDING_PROVIDER=mnn"
echo "  - MNN_EMBEDDING_MODEL_PATH=$MODEL_DIR/embedding.mnn"
echo "  - BM25_WEIGHT=0.2 / VECTOR_WEIGHT=0.8"
echo "  - AGENTMEMORY_III_VERSION=0.23.0-rc.4"

echo ""

# ============ 5. 验证 ============
echo "[6/7] 验证 MNN 插件"

cd "$AGENTMEMORY_DIR"

if [ -f "mnn_embedding.node" ] || [ -f "dist/assets/mnn_embedding-"*.node ]; then
  echo "  mnn_embedding.node 存在"
else
  echo "  警告:mnn_embedding.node 不在 agentmemory 目录"
  echo "  需要从 ~/mnn-binding/build/Release/ 复制,或重新编译 N-API 插件"
fi

# 测 libMNN.so 是否可加载
node -e "
try {
  const b = require('./mnn_embedding.node') || require('./dist/assets/' + require('fs').readdirSync('./dist/assets').find(f => f.startsWith('mnn_embedding')));
  const ctx = b.init('$MODEL_DIR/embedding.mnn');
  console.log('  MNN 插件正常,维度:', b.getDim(ctx));
  b.release(ctx);
} catch(e) {
  console.log('  MNN 插件验证失败:', e.message);
  console.log('  (如果 dist/assets 里有插件,构建后会正常)');
}
" 2>&1 || true

echo ""

# ============ 6. 启动 ============
echo "[7/7] 启动 agentmemory"

if [ -f "agentmemory_ctl.py" ]; then
  python3 agentmemory_ctl.py start
else
  echo "  未找到 agentmemory_ctl.py,手动启动:"
  echo "  cd $AGENTMEMORY_DIR && node dist/cli.mjs"
fi

echo ""
echo "== 部署完成 =="
echo ""
echo "后续管理:"
echo "  python3 agentmemory_ctl.py start     # 启动"
echo "  python3 agentmemory_ctl.py stop      # 停止"
echo "  python3 agentmemory_ctl.py restart   # 重启"
echo "  python3 agentmemory_ctl.py status    # 状态"
