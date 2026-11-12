const DEFAULT_AGENT = Object.freeze({
  enabled: true, url: 'http://127.0.0.1:8000', timeout: 35000,
  maxConcurrent: 2, requestsPerMinute: 20, playerCooldown: 3000
});
const INTENTS = new Set(['conversation', 'query_status', 'follow', 'goto', 'stop', 'unsupported', 'clarification']);

function validIntent(data) {
  if (!data || !INTENTS.has(data.intent) || typeof data.reply !== 'string' || !data.reply.trim() || data.reply.length > 500) return false;
  if (Object.keys(data).some((key) => !['intent', 'arguments', 'reply'].includes(key))) return false;
  const args = data.arguments;
  if (!args || typeof args !== 'object' || Array.isArray(args)) return false;
  if (data.intent !== 'goto') return Object.keys(args).length === 0;
  return Object.keys(args).length === 3 && ['x', 'y', 'z'].every((key) => Number.isFinite(args[key])) &&
    Math.abs(args.x) <= 29999984 && Math.abs(args.z) <= 29999984 && args.y >= -2048 && args.y <= 2047;
}

function safeReply(reply) {
  const text = reply.replace(/[\u0000-\u001f\u007f§]/gu, ' ')
    .replace(/\bsk-[A-Za-z0-9_-]+/gu, '[已隐藏]').trim();
  // 前缀保证以 / 开头的模型文本也只会发送为普通聊天，不会变成游戏命令。
  return `MineMate：${text}`.slice(0, 240);
}

function createAgentClient(config = {}, { fetchImpl = fetch, clock = Date.now, logger = console } = {}) {
  const options = { ...DEFAULT_AGENT, ...config };
  const controllers = new Set();
  const cancelled = new WeakSet();
  let disposed = false;
  let windowStart = clock();
  let count = 0;
  const players = new Map();

  async function message(player, text, context = {}) {
    if (disposed || !options.enabled) return null;
    if (typeof text !== 'string' || text.length > 500 || !text.trim()) return { reply: '消息长度须为 1–500 个字符。' };
    if (!/^[A-Za-z0-9_]{3,16}$/.test(player)) return null;
    const now = clock();
    if (now - windowStart >= 60000) { windowStart = now; count = 0; }
    for (const [name, expiry] of players) if (expiry <= now) players.delete(name);
    const name = player.toLowerCase();
    // 无队列，拒绝过量请求且不逐条回复，防止刷屏放大。
    if (controllers.size >= options.maxConcurrent || count >= options.requestsPerMinute || players.has(name) || players.size >= 512) return null;
    players.set(name, now + options.playerCooldown);
    count += 1;
    const controller = new AbortController();
    controller.player = name;
    controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), options.timeout);
    try {
      const response = await fetchImpl(`${options.url}/api/agent/message`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ player, message: text, ...(Number.isInteger(context.generation) ? { generation: context.generation } : {}) }), signal: controller.signal,
        redirect: 'error'
      });
      // 有界读取响应，超时同时覆盖响应体；不信任上游提供的错误文本。
      let size = 0;
      const chunks = [];
      for await (const chunk of response.body) {
        size += chunk.length;
        if (size > 16384) { controller.abort(); throw new Error('invalid response'); }
        chunks.push(Buffer.from(chunk));
      }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (cancelled.has(controller)) return null;
      if (!response.ok || body.success !== true || body.error !== null || !validIntent(body.data)) {
        logger.error('[Agent] 服务返回失败或无效响应。');
        return { reply: response.status === 429 ? '自然语言服务繁忙，请稍后再试。' : '自然语言服务暂不可用，请检查 Python 服务和模型配置；旧指令仍可使用。' };
      }
      return { ...body.data, reply: safeReply(body.data.reply) };
    } catch {
      if (disposed || cancelled.has(controller)) return null;
      logger.error('[Agent] 请求失败或超时，请检查 Python 服务。');
      return { reply: '自然语言服务连接失败或超时；旧指令仍可使用。' };
    } finally {
      clearTimeout(timer);
      controllers.delete(controller);
    }
  }

  function dispose() {
    disposed = true;
    for (const controller of controllers) controller.abort();
    players.clear();
  }
  function cancel(player) {
    for (const controller of controllers) if (controller.player === player.toLowerCase()) {
      cancelled.add(controller); controller.abort();
    }
  }
  return { message, dispose, cancel };
}

module.exports = { createAgentClient, DEFAULT_AGENT, validIntent, safeReply };
