const { object, position } = require('../schema');
const { blockAt, ensure, state } = require('../common');
const { withWindow } = require('./window');
const adapters = [require('./adapters/workbench'), require('./adapters/furnace'), require('./adapters/container')];
async function interact(ctx, p, type, use) {
  const block = blockAt(ctx, p);
  const adapter = adapters.find((entry) => entry.type === type && entry.matches(block));
  ensure(adapter, 'WRONG_BLOCK', '目标不是当前支持的指定功能方块。');
  return withWindow(ctx, () => adapter.open(ctx.bot, block), (window) => use(window, adapter, block));
}
function registerInteraction(registry) {
  for (const type of ['workbench', 'furnace', 'container']) {
    registry.register({ name: `open_${type}`, description: `打开并检查 ${type} 窗口后关闭；只返回可序列化快照，不保留窗口句柄。`, parameters: object({ position }),
      execute: (ctx, a) => interact(ctx, a.position, type, (window, adapter) => state('completed', adapter.inspect(window))) });
  }
  registry.register({ name: 'interact_block', description: '交互明确指定的门、按钮或拉杆；功能方块使用独立窗口工具。', parameters: object({ position }),
    async execute(ctx, a) {
      const block = blockAt(ctx, a.position);
      ensure(/(?:_door|_button)$|^lever$/.test(block.name), 'UNSUPPORTED_BLOCK', '普通交互仅支持门、按钮和拉杆。');
      const before = JSON.stringify(block.getProperties());
      await ctx.step(() => ctx.bot.activateBlock(block));
      await ctx.pause(200);
      const after = ctx.bot.blockAt(block.position);
      const changed = after && JSON.stringify(after.getProperties()) !== before;
      return state(changed ? 'completed' : 'uncertain', { position: a.position, changed: Boolean(changed) });
    }
  });
}
module.exports = { registerInteraction, interact, adapters };
