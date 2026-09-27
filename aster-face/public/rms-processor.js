// AudioWorklet — computes RMS amplitude on the audio thread.
// Runs even while the window is minimized (unlike requestAnimationFrame),
// so the mini reactor can keep lip-syncing to Aster's voice.
class RMSProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._blocks = 0;
    this._acc = 0;
    this._count = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) {
      const ch = input[0];
      let sum = 0;
      for (let i = 0; i < ch.length; i++) sum += ch[i] * ch[i];
      this._acc += sum;
      this._count += ch.length;
      this._blocks++;
      if (this._blocks >= 8) {
        // ~8 × 128 samples ≈ 21 ms between posts
        this.port.postMessage(this._count ? Math.sqrt(this._acc / this._count) : 0);
        this._blocks = 0;
        this._acc = 0;
        this._count = 0;
      }
    }
    return true;
  }
}

registerProcessor("rms-processor", RMSProcessor);
