import type { EmbeddingProvider } from "../../types.js";

type MNNBinding = {
  init(modelPath: string): unknown;
  embed(ctx: unknown, text: string): Float32Array;
  getDim(ctx: unknown): number;
  release(ctx: unknown): void;
};

let binding: MNNBinding | null = null;
let ctx: unknown = null;

function getBinding(): MNNBinding {
  if (binding) return binding;
  try {
    binding = require("../../../mnn_embedding.node") as MNNBinding;
  } catch (err) {
    throw new Error(
      "MNN embedding plugin not found. Copy mnn_embedding.node to the agentmemory root directory.",
    );
  }
  return binding;
}

export class MNNEmbeddingProvider implements EmbeddingProvider {
  readonly name = "mnn";
  readonly dimensions = 1024;
  private modelPath: string;

  constructor(modelPath?: string) {
    this.modelPath =
      modelPath ||
      process.env["MNN_EMBEDDING_MODEL_PATH"] ||
      "/data/data/com.termux/files/home/mnn/bge-large-zh/embedding.mnn";
  }

  private getContext() {
    if (ctx) return ctx;
    const b = getBinding();
    ctx = b.init(this.modelPath);
    const dim = b.getDim(ctx);
    if (dim !== this.dimensions) {
      b.release(ctx);
      ctx = null;
      throw new Error(
        `MNN model dimension mismatch: expected ${this.dimensions}, got ${dim}`,
      );
    }
    return ctx;
  }

  async embed(text: string): Promise<Float32Array> {
    const b = getBinding();
    return b.embed(this.getContext(), text);
  }

  async embedBatch(texts: string[]): Promise<Float32Array[]> {
    const b = getBinding();
    const c = this.getContext();
    return texts.map((t) => b.embed(c, t));
  }
}
