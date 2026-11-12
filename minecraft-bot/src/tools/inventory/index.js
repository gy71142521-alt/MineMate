const { object, text, number } = require('../schema');
const { ensure, count, itemByName, items, state } = require('../common');
const { validEntity } = require('../combat/helpers');
const DROPPABLE = new Set(['dirt', 'cobblestone', 'gravel', 'sand', 'netherrack', 'rotten_flesh']);
async function consume(ctx, name) {
  const { bot } = ctx, item = itemByName(bot, name), food = bot.registry.foodsByName?.[name];
  ensure(food || name === 'milk_bucket' || name === 'honey_bottle', 'UNSUPPORTED_ITEM', '当前只支持已知食物、蜂蜜或牛奶；其他饮品效果需另行适配。');
  ensure(!food || bot.food < 20, 'CANNOT_CONSUME', '饥饿值已满，当前不使用食物。');
  const before = { count: count(bot, name), health: bot.health, food: bot.food };
  await ctx.step(() => bot.equip(item, 'hand'));
  ctx.cleanup(() => bot.deactivateItem());
  await ctx.step(() => bot.consume());
  const after = { count: count(bot, name), health: bot.health, food: bot.food };
  return state(after.count < before.count ? 'completed' : 'uncertain', { item: name, before, after });
}
function registerInventory(registry) {
  registry.register({ name: 'equip_item', description: '装备指定背包物品并验证装备槽。',
    parameters: object({ item_name: text, destination: { type: 'string', enum: ['hand', 'off-hand', 'head', 'torso', 'legs', 'feet'] } }),
    async execute(ctx, a) {
      const item = itemByName(ctx.bot, a.item_name);
      await ctx.step(() => ctx.bot.equip(item, a.destination));
      const actual = a.destination === 'hand' ? ctx.bot.heldItem : ctx.bot.inventory.slots[ctx.bot.getEquipmentDestSlot(a.destination)];
      return state(actual?.name === a.item_name ? 'completed' : 'uncertain', { destination: a.destination, actualItem: actual?.name ?? null });
    }
  });
  registry.register({ name: 'consume_item', description: '使用已知食物或牛奶，验证背包数量及生命/饥饿变化。', parameters: object({ item_name: text }), execute: (ctx, a) => consume(ctx, a.item_name) });
  registry.register({ name: 'drop_item', description: '只丢弃最多 16 个普通低价值物品，不支持重要物品或整个背包。',
    parameters: object({ item_name: text, count: number(1, 16, true) }),
    async execute(ctx, a) {
      ensure(DROPPABLE.has(a.item_name), 'PROTECTED_ITEM', '该物品不在允许丢弃的低价值白名单。');
      const item = itemByName(ctx.bot, a.item_name), before = count(ctx.bot, a.item_name);
      ensure(before >= a.count, 'INSUFFICIENT_ITEMS', '物品数量不足。');
      ensure(items(ctx.bot).reduce((sum, value) => sum + value.count, 0) > a.count, 'EMPTY_INVENTORY_FORBIDDEN', '不能丢弃全部背包。');
      await ctx.step(() => ctx.bot.toss(item.type, null, a.count));
      const delta = before - count(ctx.bot, a.item_name);
      return state(delta === a.count ? 'completed' : 'uncertain', { item: a.item_name, dropped: delta });
    }
  });
  registry.register({ name: 'use_item', description: '按类型使用食物/牛奶、短暂举盾或对指定羊使用剪刀；其他道具拒绝执行。',
    parameters: object({ item_name: text, entity_id: number(0, 2147483647, true) }, ['item_name']),
    async execute(ctx, a) {
      const { bot } = ctx;
      if (bot.registry.foodsByName?.[a.item_name] || ['milk_bucket', 'honey_bottle'].includes(a.item_name)) return consume(ctx, a.item_name);
      ensure(['shield', 'shears'].includes(a.item_name), 'UNSUPPORTED_ITEM', '该道具尚无安全的使用适配器。');
      const item = itemByName(bot, a.item_name);
      await ctx.step(() => bot.equip(item, 'hand'));
      if (a.item_name === 'shield') {
        ctx.cleanup(() => bot.deactivateItem());
        bot.activateItem(); await ctx.pause(500);
        return state('uncertain', { item: a.item_name, reason: '已发送短暂举盾操作，未确认服务器防御状态。' });
      }
      const entity = bot.entities[a.entity_id];
      ensure(validEntity(bot, entity, 3) && entity.name === 'sheep', 'INVALID_TARGET', '剪刀只支持明确指定的附近有效羊实体。');
      const before = JSON.stringify(entity.metadata);
      await ctx.step(() => bot.lookAt(entity.position));
      ensure(validEntity(bot, entity, 3), 'ENTITY_MISSING', '目标已经失效。');
      bot.useOn(entity); await ctx.pause(300);
      return state('uncertain', { item: a.item_name, entityId: entity.id, metadataChanged: before !== JSON.stringify(entity.metadata) });
    }
  });
}
module.exports = registerInventory;
