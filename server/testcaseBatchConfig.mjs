function boundedPositiveInteger(value, fallback, maximum) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) return fallback;
  return Math.min(parsed, maximum);
}

export function resolveTestcaseBatchConfig(env = process.env) {
  return {
    batchSize: boundedPositiveInteger(env.TESTCASE_GENERATION_BATCH_SIZE, 60, 100),
    concurrency: boundedPositiveInteger(env.TESTCASE_GENERATION_CONCURRENCY, 12, 16),
  };
}