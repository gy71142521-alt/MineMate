const { object } = require('../schema');
const { ensure, state } = require('../common');

module.exports = function registerMovementControls(registry) {
  for (const [name, method, description] of [
    ['set_sprint', 'setSprint', '设置跑步许可 enabled（布尔值）。不会自己向前走；可与跟随/导航组合，寻路器只在安全路段跑步。潜行时暂时禁止跑步。'],
    ['set_sneak', 'setSneak', '开启/关闭持续潜行 enabled（布尔值），可与跟随/导航组合。关闭后恢复先前跑步设置；停止、失败、死亡或断线会清理模式。']
  ]) {
    registry.register({ name, description, parameters: object({ enabled: { type: 'boolean' } }), movement: true, control: true,
      execute: ({ movement, username }, { enabled }) => {
        const result = movement[method](username, enabled);
        ensure(result?.ok, 'MOVEMENT_REJECTED', result?.message ?? '移动模式被拒绝。');
        return state('completed', { motion: result.motion });
      } });
  }
  registry.register({ name: 'jump', description: '在地面执行一次跳跃，最多等待 3 秒确认起跳和落地，不持续按住。可在跟随/导航期间使用；不会新增寻路目标。',
    parameters: object(), movement: true, preserveMovement: true,
    execute: async (ctx) => {
      const result = await ctx.step(() => ctx.movement.jump(ctx.username, { signal: ctx.signal }));
      ensure(result?.ok, 'JUMP_FAILED', result?.message ?? '未确认跳跃完成。');
      return state('completed', { movement: result });
    } });
};
