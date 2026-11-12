const { object, text, number } = require('../schema');
const { ensure, distance, count, state } = require('../common');
const { goals: { GoalNear } } = require('mineflayer-pathfinder');
module.exports = function registerCollect(registry) {
  registry.register({ name: 'collect_item', description: '在 8 格内拾取指定物品实体；不挖掘，必须确认背包增量。',
    parameters: object({ item_name: text, radius: number(1, 8), entity_id: number(0, 2147483647, true) }, ['item_name']),
    async execute(ctx, a) {
      const { bot } = ctx;
      const candidates = Object.values(bot.entities).filter((entity) => entity?.isValid && entity.position && entity.getDroppedItem?.()?.name === a.item_name &&
        (a.entity_id === undefined || entity.id === a.entity_id) && distance(bot.entity.position, entity.position) <= (a.radius ?? 8))
        .sort((x, y) => distance(bot.entity.position, x.position) - distance(bot.entity.position, y.position));
      const entity = candidates[0];
      ensure(entity, 'ITEM_ENTITY_MISSING', '范围内没有指定的有效掉落物实体。');
      ensure(bot.inventory.emptySlotCount() > 0 || bot.inventory.items().some((item) => item.name === a.item_name && item.count < item.stackSize), 'INVENTORY_FULL', '背包没有可接收物品的空间。');
      const before = count(bot, a.item_name);
      const goal = new GoalNear(entity.position.x, entity.position.y, entity.position.z, 0.5);
      ctx.cleanup(() => { if (bot.pathfinder.goal === goal) { bot.pathfinder.setGoal(null); bot.clearControlStates(); } });
      bot.pathfinder.setGoal(goal);
      for (let i = 0; i < 50; i++) {
        await ctx.pause(200);
        const delta = count(bot, a.item_name) - before;
        if (delta > 0) return state('completed', { item: a.item_name, collected: delta, entityId: entity.id });
        if (!entity.isValid || bot.entities[entity.id] !== entity) return state('uncertain', { item: a.item_name, collected: 0, reason: '实体消失但未确认背包增量。' });
        ensure(distance(bot.entity.position, entity.position) <= (a.radius ?? 8), 'OUT_OF_RANGE', '掉落物离开本次拾取范围。');
      }
      return state('uncertain', { item: a.item_name, collected: 0, reason: '未在有限等待内确认拾取。' });
    }
  });
};
