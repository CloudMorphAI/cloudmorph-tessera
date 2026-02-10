/**
 * In-memory token-bucket rate limiter for MCP server.
 *
 * Each unique token gets its own bucket with configurable:
 *   - requestsPerDay  (daily cap, reset at midnight UTC)
 *   - burstPerMinute  (short-term burst protection)
 *   - concurrentJobs  (max in-flight jobs)
 *
 * For production, swap the in-memory maps with DynamoDB atomic counters.
 */

export type RateLimitConfig = {
  requestsPerDay: number;
  burstPerMinute: number;
  concurrentJobs: number;
};

type TokenBucket = {
  dailyCount: number;
  dailyResetAt: number; // epoch ms of next midnight UTC
  minuteCount: number;
  minuteResetAt: number; // epoch ms of next minute boundary
  concurrentJobs: number;
};

type RateLimitResult = {
  allowed: boolean;
  reason?: string;
  retryAfterSeconds?: number;
  remaining?: {
    daily: number;
    burst: number;
    concurrent: number;
  };
};

const DEFAULT_CONFIG: RateLimitConfig = {
  requestsPerDay: 100,
  burstPerMinute: 30,
  concurrentJobs: 1,
};

function nextMidnightUtc(): number {
  const now = new Date();
  const tomorrow = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1),
  );
  return tomorrow.getTime();
}

function nextMinuteBoundary(): number {
  const now = Date.now();
  return now + (60_000 - (now % 60_000));
}

export class RateLimiter {
  private buckets = new Map<string, TokenBucket>();
  private config: RateLimitConfig;
  private cleanupInterval: NodeJS.Timeout;

  constructor(config?: Partial<RateLimitConfig>) {
    this.config = { ...DEFAULT_CONFIG, ...config };

    // Periodic cleanup of stale buckets (every 5 minutes)
    this.cleanupInterval = setInterval(() => {
      const now = Date.now();
      const staleThreshold = now - 3_600_000; // 1 hour
      this.buckets.forEach((bucket, key) => {
        if (bucket.dailyResetAt < staleThreshold && bucket.concurrentJobs === 0) {
          this.buckets.delete(key);
        }
      });
    }, 300_000);
  }

  private getBucket(tokenHash: string): TokenBucket {
    let bucket = this.buckets.get(tokenHash);
    const now = Date.now();

    if (!bucket) {
      bucket = {
        dailyCount: 0,
        dailyResetAt: nextMidnightUtc(),
        minuteCount: 0,
        minuteResetAt: nextMinuteBoundary(),
        concurrentJobs: 0,
      };
      this.buckets.set(tokenHash, bucket);
      return bucket;
    }

    // Reset daily counter
    if (now >= bucket.dailyResetAt) {
      bucket.dailyCount = 0;
      bucket.dailyResetAt = nextMidnightUtc();
    }

    // Reset minute counter
    if (now >= bucket.minuteResetAt) {
      bucket.minuteCount = 0;
      bucket.minuteResetAt = nextMinuteBoundary();
    }

    return bucket;
  }

  /**
   * Check if a request is allowed. Does NOT consume a slot;
   * call `consumeRequest()` after the request is accepted.
   */
  checkRequest(tokenHash: string): RateLimitResult {
    const bucket = this.getBucket(tokenHash);
    const remaining = {
      daily: Math.max(0, this.config.requestsPerDay - bucket.dailyCount),
      burst: Math.max(0, this.config.burstPerMinute - bucket.minuteCount),
      concurrent: Math.max(0, this.config.concurrentJobs - bucket.concurrentJobs),
    };

    if (bucket.dailyCount >= this.config.requestsPerDay) {
      const retryAfter = Math.ceil((bucket.dailyResetAt - Date.now()) / 1000);
      return {
        allowed: false,
        reason: "daily_limit_exceeded",
        retryAfterSeconds: Math.max(1, retryAfter),
        remaining,
      };
    }

    if (bucket.minuteCount >= this.config.burstPerMinute) {
      const retryAfter = Math.ceil((bucket.minuteResetAt - Date.now()) / 1000);
      return {
        allowed: false,
        reason: "burst_limit_exceeded",
        retryAfterSeconds: Math.max(1, retryAfter),
        remaining,
      };
    }

    return { allowed: true, remaining };
  }

  /**
   * Check if a new concurrent job is allowed.
   */
  checkConcurrentJob(tokenHash: string): RateLimitResult {
    const bucket = this.getBucket(tokenHash);
    const remaining = {
      daily: Math.max(0, this.config.requestsPerDay - bucket.dailyCount),
      burst: Math.max(0, this.config.burstPerMinute - bucket.minuteCount),
      concurrent: Math.max(0, this.config.concurrentJobs - bucket.concurrentJobs),
    };

    if (bucket.concurrentJobs >= this.config.concurrentJobs) {
      return {
        allowed: false,
        reason: "concurrent_job_limit_exceeded",
        remaining,
      };
    }

    return { allowed: true, remaining };
  }

  /** Consume a request slot (daily + burst). */
  consumeRequest(tokenHash: string): void {
    const bucket = this.getBucket(tokenHash);
    bucket.dailyCount += 1;
    bucket.minuteCount += 1;
  }

  /** Increment concurrent job count. */
  acquireJob(tokenHash: string): void {
    const bucket = this.getBucket(tokenHash);
    bucket.concurrentJobs += 1;
  }

  /** Decrement concurrent job count (called when job completes). */
  releaseJob(tokenHash: string): void {
    const bucket = this.getBucket(tokenHash);
    bucket.concurrentJobs = Math.max(0, bucket.concurrentJobs - 1);
  }

  /** Get current usage stats for a token. */
  getUsage(tokenHash: string): {
    dailyCount: number;
    minuteCount: number;
    concurrentJobs: number;
    limits: RateLimitConfig;
  } {
    const bucket = this.getBucket(tokenHash);
    return {
      dailyCount: bucket.dailyCount,
      minuteCount: bucket.minuteCount,
      concurrentJobs: bucket.concurrentJobs,
      limits: { ...this.config },
    };
  }

  /** Update config (e.g., when loading plan limits). */
  updateConfig(config: Partial<RateLimitConfig>): void {
    Object.assign(this.config, config);
  }

  /** Shutdown cleanup interval. */
  close(): void {
    clearInterval(this.cleanupInterval);
  }
}

/**
 * Hash a token to a stable key for bucket lookup.
 * Uses a simple hash to avoid storing raw tokens in memory.
 */
export function hashToken(token: string): string {
  let hash = 0;
  for (let i = 0; i < token.length; i++) {
    const char = token.charCodeAt(i);
    hash = ((hash << 5) - hash + char) | 0;
  }
  // Include token prefix + length for extra differentiation
  const prefix = token.slice(0, 8);
  return `${prefix}:${hash.toString(36)}`;
}
