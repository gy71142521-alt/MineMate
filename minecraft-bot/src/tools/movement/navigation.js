const { Vec3 } = require('vec3');
const { ensure, ToolError, state } = require('../common');

// 只为工具调用等待现有移动控制器的真实结果，不创建第二个寻路目标。
async function navigate(ctx, args) {
  const { bot, movement, username } = ctx;
  const waitMs = (args.wait_seconds ?? 15) * 1000;
  const canWait = waitMs > 0 && typeof movement.on === 'function' && typeof movement.removeListener === 'function';
  let taskId = null, earlyResult, timer, started, resolveWait, rejectWait;
  const completed = new Promise((resolve, reject) => { resolveWait = resolve; rejectWait = reject; });
  const onResult = (result) => {
    if (taskId === null) { earlyResult = result; return; }
    if (result.taskId !== taskId) return;
    if (result.ok && result.status === 'arrived') resolveWait(state('completed', { movement: result }));
    else rejectWait(new ToolError('NAVIGATION_FAILED', result.message ?? '导航未完成，不能继续后续动作。'));
  };
  if (canWait) movement.on('result', onResult);
  ctx.cleanup(() => {
    if (!ctx.signal.aborted) return;
    // 停止只影响此工具创建的目标，迟到的取消不能停止新任务。
    try {
      if (taskId !== null && movement.getState?.().taskId === taskId) movement.stop(username);
    } finally { rejectWait(ctx.signal.reason); }
  });
  // 在尚未开始等待就被取消/拒绝的情况下也观察拒绝，避免未处理的 Promise。
  completed.catch(() => {});
  try {
    ctx.check();
    // arrival_range=0 表示必须站进指定脚部方块。若那里或头部被实心方块占用，
    // 继续交给寻路器只会得到笼统的 NAVIGATION_FAILED，模型还可能反复重试。
    if ((args.arrival_range ?? 2) === 0) {
      const feet = bot.blockAt(new Vec3(args.x, args.y, args.z));
      const head = bot.blockAt(new Vec3(args.x, args.y + 1, args.z));
      ensure(feet && feet.boundingBox === 'empty', 'TARGET_OCCUPIED', '目标脚部位置被方块占用，不能直接导航进去；先挖开或换站位。');
      ensure(head && head.boundingBox === 'empty', 'TARGET_OCCUPIED', '目标位置头部空间不足，不能直接导航进去。');
    }
    started = args.arrival_range === undefined
      ? movement.goTo(username, args.x, args.y, args.z)
      : movement.goTo(username, args.x, args.y, args.z, { arrivalRange: args.arrival_range });
    ensure(started?.ok, 'MOVEMENT_REJECTED', started?.message ?? '移动被拒绝。');
    taskId = Number.isSafeInteger(started.taskId) ? started.taskId : null;
    if (!canWait || taskId === null) return state('pending', { movement: started });
    if (earlyResult) onResult(earlyResult);
    timer = setTimeout(() => resolveWait(state('pending', { movement: started, note: '有限等待结束，导航尚未确认到达，后续动作未执行。' })), waitMs);
    return await ctx.step(() => completed);
  } finally {
    clearTimeout(timer);
    if (canWait) movement.removeListener('result', onResult);
  }
}

module.exports = { navigate };
