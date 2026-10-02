/** A slower earlier read cannot replace a newer snapshot or revive a disposed view. */
export class LatestRequest {
  private version = 0;

  invalidate() {
    this.version++;
  }

  async run<T>(request: () => Promise<T>, apply: (value: T) => void): Promise<void> {
    const version = ++this.version;
    try {
      const value = await request();
      if (version === this.version) apply(value);
    } catch (error) {
      if (version === this.version) throw error;
    }
  }
}
