const { validate } = require('./schema');
const { ToolError, ensure } = require('./common');
const { createSession } = require('./session');

function createRegistry(bot, { movement, perception, ownerUsername = '', logger = console, timeoutMs = 20000, leaseTtlMs = 30000, now = Date.now } = {}) {
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
  const session = createSession({ access, ttlMs: leaseTtlMs, now,
    isReady: () => !disposed && ready && bot.entity && bot.isAlive !== false,
    isBusy: () => Boolean(active || movement?.getState?.().type && movement.getState().type !== 'idle'),
    onRevoke: (previous, reason) => {
      try {
        // 租约仍属于原任务时统一释放持续模式和移动；手动新目标已先撤销旧租约，不走这里。
        movement?.stop(previous.username);
      } catch { logger.error('[Tools] 租约撤销后停止移动失败。'); }
      finally {
        // 正常 release 是任务边界，后续 Runtime 步骤仍属于同一代任务。
        // 过期/异常撤销才递增 generation 并使旧请求失效。
        if (reason !== 'released') cancel(previous.username, true);
      }
    } });
  function cancel(username, internal = false, requestId = null) {
    if (!internal && !access(username)) return false;
    if (requestId && active?.requestId !== requestId && pendingMovement?.requestId !== requestId) return true;
    session.revoke(false);
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
  async function run(name, args, username, requestId, context) {
    const definition = definitions.get(name);
    if (!definition) return fail(name, 'UNKNOWN_TOOL', '工具未注册。');
    if (!validate(definition.parameters, args)) return fail(name, 'INVALID_ARGUMENT', '工具参数不符合 Schema。');
    try { session.authorize(context, username, definition.mutates !== false, definition.priority); }
    catch (error) { return fail(name, error.code, error.message); }
    if (!access(username) && definition.mutates !== false) return fail(name, 'FORBIDDEN', '只有配置的主人可以执行游戏操作。');
    if (disposed || !ready || !bot.entity || bot.isAlive === false) return fail(name, 'BOT_NOT_READY', 'Bot 尚未就绪或已断线。');
    if (definition.priority) {
      cancel(username);
      try { return { success: true, tool: name, data: definition.execute({ bot, movement, perception, username }, args), error: null }; }
      catch { return fail(name, 'EXECUTION_FAILED', '停止操作失败。'); }
    }
    // 模式是同步控制配置，可在导航等待期间调整，但不能插入挖掘等动作。
    if (definition.control) {
      if (active && !active.compatibleControls) return fail(name, 'BUSY', 'Bot 正在执行另一项操作，请先停止。');
      try {
        logger.log(`[Tools] 开始 ${name}`);
        const data = definition.execute({ bot, movement, perception, username }, args);
        logger.log(`[Tools] 结束 ${name}：${data.status}`);
        return { success: true, tool: name, data, error: null };
      } catch (error) {
        logger.error(`[Tools] ${name}：MOVEMENT_REJECTED`);
        return fail(name, 'MOVEMENT_REJECTED', error instanceof ToolError ? error.message : '移动模式设置失败。');
      }
    }
    if (definition.mutates !== false && active) return fail(name, 'BUSY', 'Bot 正在执行另一项操作，请先停止。');
    if (running >= 4) return fail(name, 'BUSY', '并发工具请求已达到上限。');
    running += 1;
    const controller = new AbortController();
    const token = { controller, name, username, requestId, compatibleControls: definition.compatibleControls === true };
    if (definition.mutates !== false) { active = token; if (!definition.preserveMovement) pendingMovement = null; }
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
        if (definition.movement && data.status === 'pending') pendingMovement = { requestId, taskId: movement?.getState?.().taskId, leaseId: context.leaseId };
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
      if (known) {
        logger.error(`[Tools] ${name} 失败 code=${error.code} request=${requestId ?? '-'} detail=${error.message}`);
      } else {
        // 未预期异常保留堆栈，便于定位；已知工具错误只输出程序定义的 code/message。
        logger.error(`[Tools] ${name} 未预期异常 request=${requestId ?? '-'}`, error);
      }
      return fail(name, known ? error.code : 'EXECUTION_FAILED', known ? error.message : '工具执行失败，请查询游戏状态。');
    } finally { controller.signal.removeEventListener('abort', onAbort); }
  }
  function execute(name, args, username, requestId, context = {}) {
    try { session.authorize(context, username, definitions.get(name)?.mutates !== false, definitions.get(name)?.priority); }
    catch (error) { return Promise.resolve(fail(name, error.code, error.message)); }
    // 已执行记录保留十分钟；容量达到上限时拒绝新操作，绝不驱逐在途记录以重复执行。
    for (const [key, entry] of requests) if (entry.settledAt && now() - entry.settledAt >= 600000 && active?.requestId !== entry.requestId) requests.delete(key);
    const key = requestId ? `${username?.toLowerCase()}:${requestId}` : null;
    const fingerprint = JSON.stringify([name, args]);
    if (key && requests.has(key)) {
      const previous = requests.get(key);
      return previous.fingerprint === fingerprint ? previous.promise : Promise.resolve(fail(name, 'REQUEST_CONFLICT', '请求 ID 已被其他操作使用。'));
    }
    if (key && requests.size >= 512) return Promise.resolve(fail(name, 'REQUEST_LIMIT', '请求记录已满，请稍后再试。'));
    const promise = run(name, args, username, requestId, context);
    if (key) {
      const entry = { promise, fingerprint, requestId, result: null, settledAt: null };
      requests.set(key, entry);
      promise.then((value) => { entry.result = value; entry.settledAt = now(); });
    }
    return promise;
  }
  function guardMovement() {
    if (!movement) return;
    for (const method of ['follow', 'goTo', 'setSprint', 'setSneak', 'jump']) {
      if (typeof publicMovement[method] !== 'function') continue;
      const original = publicMovement[method].bind(publicMovement);
      publicMovement[method] = (...args) => {
        const control = ['setSprint', 'setSneak'].includes(method);
        if ((session.inspect() && (control || method === 'jump')) || active && !(control && active.compatibleControls && !session.inspect())) {
          const denied = { ok: false, status: 'busy', message: 'Bot 正在执行操作，请先停止。' };
          return method === 'jump' ? Promise.resolve(denied) : denied;
        }
        const result = original(...args);
        if (['follow', 'goTo'].includes(method) && result?.ok && access(args[0])) cancel(args[0]);
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
  for (const method of ['follow', 'goTo', 'setSprint', 'setSneak', 'jump']) if (typeof movement?.[method] === 'function') internalMovement[method] = movement[method].bind(movement);
  // 等待导航使用原控制器的事件存储，不能在 Object.create 的包装对象上创建第二套监听状态。
  for (const method of ['on', 'removeListener']) if (typeof movement?.[method] === 'function') internalMovement[method] = movement[method].bind(movement);
  movement = internalMovement;
  const onSpawn = () => { ready = true; };
  const onPause = () => { ready = false; cancel(null, true); };
  const onEnd = () => { disposed = true; onPause(); for (const [event, handler] of Object.entries(listeners)) bot.removeListener(event, handler); };
  const listeners = { spawn: onSpawn, death: onPause, respawn: onPause, kicked: onEnd, end: onEnd };
  for (const [event, handler] of Object.entries(listeners)) bot.on(event, handler);
  return { register, list, execute, cancel, guardMovement,
    lease: (action, username, context) => {
      session.inspect();
      ensure(context.generation === generation, 'STALE_TASK', '任务已被停止或生命周期变化取消。');
      return session.update(action, username, context);
    },
    receipt: (username, requestId, context = {}) => {
      session.authorize(context, username, false);
      ensure(access(username), 'FORBIDDEN', '无权查询请求记录。');
      const entry = requests.get(`${username.toLowerCase()}:${requestId}`);
      return entry ? { requestId, result: entry.result ? structuredClone(entry.result) : null,
        running: active?.requestId === requestId || !entry.settledAt, instanceId: session.instanceId } : null;
    },
    getState: () => { const lease = session.inspect(); return { active: active?.name ?? null, generation, instanceId: session.instanceId, lease }; }, dispose: onEnd };
}
module.exports = { createRegistry };
