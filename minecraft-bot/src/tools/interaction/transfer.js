const { object, text, number, position } = require('../schema');
const { ensure, count, state } = require('../common');
const { interact } = require('./index');
function registerTransfer(registry) {
  registry.register({ name: 'transfer_item', description: '在指定箱子或木桶中存入/取出最多 16 个物品，验证背包和容器双方增减。',
    parameters: object({ position, direction: { type: 'string', enum: ['deposit', 'withdraw'] }, item_name: text, count: number(1, 16, true) }),
    execute(ctx, a) {
      return interact(ctx, a.position, 'container', async (window) => {
        const { bot } = ctx, type = bot.registry.itemsByName[a.item_name]?.id;
        ensure(Number.isInteger(type), 'UNKNOWN_ITEM', '指定物品不存在。');
        const containerCount = () => window.slots.slice(0, window.inventoryStart).reduce((sum, item) => sum + (item?.name === a.item_name ? item.count : 0), 0);
        const inventoryBefore = count(bot, a.item_name), containerBefore = containerCount();
        ensure((a.direction === 'deposit' ? inventoryBefore : containerBefore) >= a.count, 'INSUFFICIENT_ITEMS', '来源物品数量不足。');
        const destinationSlots = a.direction === 'deposit' ? window.slots.slice(0, window.inventoryStart) : (Number.isInteger(window.inventoryEnd) ? window.slots.slice(window.inventoryStart, window.inventoryEnd) : bot.inventory.slots);
        ensure(destinationSlots.some((item) => !item || (item.name === a.item_name && item.count < item.stackSize)), 'NO_SPACE', '目标没有可用槽位。');
        await ctx.step(() => window[a.direction](type, null, a.count));
        const inventoryDelta = count(bot, a.item_name) - inventoryBefore, containerDelta = containerCount() - containerBefore;
        const expected = a.direction === 'deposit' ? -a.count : a.count;
        return state(inventoryDelta === expected && containerDelta === -expected ? 'completed' : 'uncertain', { item: a.item_name, inventoryDelta, containerDelta });
      });
    }
  });
}
module.exports = registerTransfer;
