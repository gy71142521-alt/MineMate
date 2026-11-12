const { validate } = require('./schema');
const { ToolError, ensure } = require('./common');

function createRegistry(bot, { movement, perception, ownerUsername = '', logger = console, timeoutMs = 20000 } = {}) {
  const publicMovement = movement;
  const definitions = new Map();
  const requests = new Map();
  let active = null;
  let disposed = false;
  let ready = false;
  let generation = 0;
  let running = 0;
  let pendingMovement = null;
  const fail = (name, code, message) => ({ success: false, tool: name, data: null, error: { code, message } });
  const access = (username) => /^[A-Za-z0-9_]{3,16}$/.test(username ?? '') && (!ownerUsername || username.toLowerCase() === ownerUsername.toLowerCase());
  function cancel(username, internal = false, requestId = null) {
    if (!internal && !access(username)) return false;
    if (requestId && active?.requestId !== requestId && pendingMovement?.requestId !== requestId) return true;
    generation += 1;
    if (requestId && pendingMovement?.requestId === requestId && movement?.getState?.().taskId === pendingMovement.taskId) movement.stop(username);
    pendingMovement = null;
    active?.controller.abort(new ToolError('CANCELLED', '操作已取消，部分操作可能已生效，请查询状态。'));
    return true;
  }
  function register(definition) {
    ensure(/^[a-z][a-z0-9_]{0,63}$/.test(definition.name) && !definitions.has(definition.name), 'DUPLICATE_TOOL', '工具名称无效或重复注册。');
    ensure(typeof definition.execute === 'function' && definition.parameters?.type === 'object', 'INVALID_TOOL', '工具定义无效。');
    definitions.set(definition.name, definition);
  }
  function list() {
    return structuredClone([...definitions.values()].map(({ name, description, parameters, mutates = true }) => ({ name, description, parameters, mutates })));
  }
  async function run(name, args, username, requestId) {
    const definition = definitions.get(name);
    if (!definition) return fail(name, 'UNKNOWN_TOOL', '工具未注册。');
    if (!validate(definition.parameters, args)) return fail(name, 'INVALID_ARGUMENT', '工具参数不符合 Schema。');
    if (!access(username) && definition.mutates !== false) return fail(name, 'FORBIDDEN', '只有配置的主人可以执行游戏操作。');
    if (disposed || !ready || !bot.entity || bot.isAlive === false) return fail(name, 'BOT_NOT_READY', 'Bot 尚未就绪或已断线。');
    if (definition.priority) {
      cancel(username);
      try { return { success: true, tool: name, data: definition.execute({ bot, movement, perception, username }, args), error: null }; }
      catch { return fail(name, 'EXECUTION_FAILED', '停止操作失败。'); }
    }
    if (definition.mutates !== false && active) return fail(name, 'BUSY', 'Bot 正在执行另一项操作，请先停止。');
    if (running >= 4) return fail(name, 'BUSY', '并发工具请求已达到上限。');
    running += 1;
    const controller = new AbortController();
    const token = { controller, name, username, requestId };
    if (definition.mutates !== false) { active = token; pendingMovement = null; }
    const cleanups = [];
    const check = () => {
      if (controller.signal.aborted) throw controller.signal.reason;
      ensure(!disposed && ready && bot.entity && bot.isAlive !== false, 'BOT_NOT_READY', 'Bot 已离开游戏或死亡。');
    };
    const ctx = {
      bot, movement, perception, username, signal: controller.signal, check,
      cleanup: (callback) => cleanups.push(callback),
      async step(operation) { check(); const value = await operation(); check(); return value; },
      pause(ms) {
        check();
        return new Promise((resolve, reject) => {
          const onAbort = () => { clearTimeout(timer); reject(controller.signal.reason); };
          const timer = setTimeout(() => { controller.signal.removeEventListener('abort', onAbort); resolve(); }, ms);
          controller.signal.addEventListener('abort', onAbort, { once: true });
        });
      }
    };
    const cleanup = () => {
      for (const callback of cleanups.splice(0)) {
        try { Promise.resolve(callback()).catch(() => logger.error(`[Tools] ${name} 清理失败，请检查 Bot 状态。`)); }
        catch { logger.error(`[Tools] ${name} 清理失败，请检查 Bot 状态。`); }
      }
    };
    controller.signal.addEventListener('abort', cleanup, { once: true });
    const timer = setTimeout(() => controller.abort(new ToolError('TIMEOUT', '工具执行超时，结果需要重新查询。')), definition.timeoutMs ?? timeoutMs);
    const work = (async () => {
      try {
        if (definition.mutates !== false && !definition.movement) {
          const stopped = movement?.stop(username);
          ensure(stopped?.ok !== false, 'MOVEMENT_FAILED', '无法停止当前移动。');
        }
        check();
        const data = await definition.execute(ctx, args);
        check();
        if (definition.movement && data.status === 'pending') pendingMovement = { requestId, taskId: movement?.getState?.().taskId };
        return { success: true, tool: name, data, error: null };
      } finally {
        clearTimeout(timer);
        cleanup();
        if (active === token) active = null;
        running -= 1;
      }
    })();
    // 超时可提前响应，但锁保留到原 API 结算，防止未完成的底层操作与新任务并发。
    let onAbort;
    const aborted = new Promise((_, reject) => {
      onAbort = () => reject(controller.signal.reason);
      controller.signal.addEventListener('abort', onAbort, { once: true });
      if (controller.signal.aborted) onAbort();
    });
    try {
      logger.log(`[Tools] 开始 ${name}`);
      const result = await Promise.race([work, aborted]);
      logger.log(`[Tools] 结束 ${name}：${result.data?.status ?? 'completed'}`);
      return JSON.parse(JSON.stringify(result));
    } catch (error) {
      const known = error instanceof ToolError;
      logger.error(`[Tools] ${name}：${known ? error.code : 'EXECUTION_FAILED'}`);
      return fail(name, known ? error.code : 'EXECUTION_FAILED', known ? error.message : '工具执行失败，请查询游戏状态。');
    } finally { controller.signal.removeEventListener('abort', onAbort); }
  }
  function execute(name, args, username, requestId) {
    const key = requestId ? `${username?.toLowerCase()}:${requestId}` : null;
    const fingerprint = JSON.stringify([name, args]);
    if (key && requests.has(key)) {
      const previous = requests.get(key);
      return previous.fingerprint === fingerprint ? previous.promise : Promise.resolve(fail(name, 'REQUEST_CONFLICT', '请求 ID 已被其他操作使用。'));
    }
    if (key && requests.size >= 128) return Promise.resolve(fail(name, 'REQUEST_LIMIT', '请求记录已满，请稍后再试。'));
    const promise = run(name, args, username, requestId);
    if (key) {
      requests.set(key, { promise, fingerprint });
      promise.finally(() => { const timer = setTimeout(() => requests.delete(key), 60000); timer.unref(); });
    }
    return promise;
  }
  function guardMovement() {
    if (!movement) return;
    for (const method of ['follow', 'goTo']) {
      if (typeof publicMovement[method] !== 'function') continue;
      const original = publicMovement[method].bind(publicMovement);
      publicMovement[method] = (...args) => {
        if (active) return { ok: false, status: 'busy', message: 'Bot 正在执行操作，请先停止。' };
        const result = original(...args);
        if (result?.ok && access(args[0])) cancel(args[0]);
        return result;
      };
    }
    if (typeof publicMovement.stop === 'function') {
      const original = publicMovement.stop.bind(publicMovement);
      publicMovement.stop = (username) => {
        if (access(username)) cancel(username);
        return original(username);
      };
    }
  }
  // registry 内部停止移动不得触发自己的取消逻辑，保存原始入口。
  const originalStop = movement?.stop?.bind(movement);
  const internalMovement = movement ? Object.create(movement) : undefined;
  if (internalMovement && originalStop) internalMovement.stop = originalStop;
  for (const method of ['follow', 'goTo']) if (typeof movement?.[method] === 'function') internalMovement[method] = movement[method].bind(movement);
  movement = internalMovement;
  const onSpawn = () => { ready = true; };
  const onPause = () => { ready = false; cancel(null, true); };
  const onEnd = () => { disposed = true; onPause(); for (const [event, handler] of Object.entries(listeners)) bot.removeListener(event, handler); };
  const listeners = { spawn: onSpawn, death: onPause, respawn: onPause, kicked: onEnd, end: onEnd };
  for (const [event, handler] of Object.entries(listeners)) bot.on(event, handler);
  return { register, list, execute, cancel, guardMovement, getState: () => ({ active: active?.name ?? null, generation }), dispose: onEnd };
}
module.exports = { createRegistry };
