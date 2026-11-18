const JUMP_TIMEOUT = 3000;

// 按键只由此控制层和 pathfinder 协作管理，不另建方向控制或寻路循环。
function createMotionControls(bot, { isReady, fail, logger }) {
  let sprint = null; // null 保留旧版 pathfinder 的自动跑步策略。
  let sneak = false;
  let jump = null;
  let pulse = false;
  let disposed = false;
  const state = () => ({ sprint, sneak, jumping: jump !== null, allowSprinting: !sneak && sprint !== false });
  function configure() {
    const movements = bot.pathfinder?.movements;
    const allowed = state().allowSprinting;
    if (movements && movements.allowSprinting !== allowed) {
      movements.allowSprinting = allowed;
      // 官方 API 重新规划速度策略，但保留当前目标及 taskId。
      bot.pathfinder.setMovements(movements);
    }
  }
  function releasePulse() {
    if (!pulse) return;
    try { bot.setControlState('jump', false); }
    finally {
      pulse = false;
      // 4.39.0 physics 源码：clearControlStates 不清除已排队的起跳。
      // 仅清除本模块发出的单次脉冲，避免取消后下一帧仍起跳。
      if ('jumpQueued' in bot) bot.jumpQueued = false;
    }
  }
  function settle(ok, status, message) {
    const current = jump;
    if (!current) return;
    jump = null;
    clearTimeout(current.timer);
    current.signal?.removeEventListener('abort', current.onAbort);
    current.resolve({ ok, status, message, username: current.username, taskId: null });
  }
  function reset(preserveModes = false) {
    settle(false, 'cancelled', '跳跃已取消。');
    try { releasePulse(); }
    finally {
      if (!preserveModes) { sprint = null; sneak = false; }
    }
  }
  function failure(message) {
    settle(false, 'failed', message);
    logger.error(`[Movement] ${message}`);
    fail(message);
  }
  function setMode(name, enabled) {
    if (name === 'sprint') sprint = enabled;
    else sneak = enabled;
    try {
      configure();
      bot.setControlState('sneak', sneak);
      if (!state().allowSprinting) bot.setControlState('sprint', false);
      return state();
    } catch {
      failure('设置移动模式失败，已停止移动。');
      return null;
    }
  }
  function applyModes() {
    if (sneak && !bot.getControlState('sneak')) bot.setControlState('sneak', true);
    if (!state().allowSprinting && bot.getControlState('sprint')) bot.setControlState('sprint', false);
  }
  function startJump(username, signal) {
    if (jump) return Promise.resolve({ ok: false, status: 'busy', message: '正在执行一次跳跃。', username });
    if (signal?.aborted) return Promise.resolve({ ok: false, status: 'cancelled', message: '跳跃已取消。', username });
    if (bot.entity?.onGround !== true || bot.entity.isInWater || bot.entity.isInLava) {
      return Promise.resolve({ ok: false, status: 'not_grounded', message: '单次跳跃需要 Bot 站在地面上且不在液体中。', username });
    }
    return new Promise((resolve) => {
      const current = { username, signal, resolve, issued: false, airborne: false, originY: bot.entity.position.y };
      jump = current;
      current.onAbort = () => { if (jump === current) failure('跳跃已取消，已停止移动。'); };
      signal?.addEventListener('abort', current.onAbort, { once: true });
      current.timer = setTimeout(() => { if (jump === current) failure('3 秒内未确认起跳并落地，已停止移动。'); }, JUMP_TIMEOUT);
    });
  }
  // physicsTick 在物理模拟之后触发。先释放上一帧的手动跳跃，再让寻路器决定必要的跳跃。
  function beforeTick() {
    try { releasePulse(); }
    catch { failure('释放跳跃按键失败，已停止移动。'); }
  }
  function afterTick() {
    if (disposed || !isReady()) return;
    try {
      applyModes();
      const current = jump;
      if (!current) return;
      if (!current.issued) {
        if (bot.entity.onGround !== true) return;
        pulse = true;
        current.originY = bot.entity.position.y;
        bot.setControlState('jump', true);
        current.issued = true;
      } else if (bot.entity.onGround === false && bot.entity.position.y > current.originY + 0.05) {
        current.airborne = true;
      } else if (current.airborne && bot.entity.onGround === true) {
        settle(true, 'jumped', '已完成一次跳跃并落地。');
      }
    } catch { failure('移动按键控制异常，已停止移动。'); }
  }
  // 注册一次，前后夹住已加载的 pathfinder 监听器，避免覆盖其下一帧决策。
  bot.prependListener('physicsTick', beforeTick);
  bot.on('physicsTick', afterTick);
  function dispose() {
    disposed = true;
    bot.removeListener('physicsTick', beforeTick);
    bot.removeListener('physicsTick', afterTick);
  }
  return { state, configure, applyModes, setMode, startJump, reset, dispose };
}

module.exports = { createMotionControls, JUMP_TIMEOUT };
