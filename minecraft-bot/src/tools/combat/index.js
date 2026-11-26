const { object, text, number, describe } = require('../schema');
const { ensure, distance, plainPosition, itemByName, usable, state } = require('../common');
const { validEntity } = require('./helpers');
// 保守白名单排除玩家和有条件敌对的中立生物。
const HOSTILE = new Set(['zombie', 'skeleton', 'creeper', 'husk', 'drowned', 'stray', 'wither_skeleton', 'silverfish', 'endermite', 'blaze', 'magma_cube', 'slime', 'ghast', 'guardian', 'elder_guardian', 'phantom', 'shulker', 'pillager', 'vindicator', 'evoker', 'ravager', 'zoglin', 'piglin_brute', 'warden']);
function registerCombat(registry) {
  registry.register({ name: 'find_entity', description: '有限半径查找指定类型实体，不包含 Bot 自身。', mutates: false,
    parameters: object({
      entity_type: describe(text, '要查找的实体类型名称，例如 zombie、cow、player；必须是当前运行时 registry 中的真实名称。'),
      radius: describe(number(1, 16), '搜索半径，单位格；默认 16。'),
      max_results: describe(number(1, 16, true), '最多返回多少个实体；默认 16。'),
    }, ['entity_type']),
    execute({ bot }, a) {
      const all = Object.values(bot.entities).filter((entity) => validEntity(bot, entity, a.radius ?? 16) && (entity.name === a.entity_type || entity.type === a.entity_type))
        .map((entity) => ({ id: entity.id, name: entity.username ?? entity.name, type: entity.type, position: plainPosition(entity.position), distance: distance(bot.entity.position, entity.position), valid: true }))
        .sort((x, y) => x.distance - y.distance);
      const maximum = a.max_results ?? 8;
      return state('completed', { entities: all.slice(0, maximum), truncated: all.length > maximum });
    }
  });
  registry.register({ name: 'attack_entity', description: '仅攻击白名单敌对生物，最多 3 次，不追击；未确认击杀不宣称死亡。',
    parameters: object({
      entity_id: describe(number(0, 2147483647, true), 'find_entity 或快照中返回的真实实体 ID；不能猜。'),
      weapon: describe(text, '可选：攻击时装备的武器名称；省略则使用当前手持物品。'),
      max_distance: describe(number(1, 3), '允许攻击的最大距离，单位格；默认 3。'),
      max_hits: describe(number(1, 3, true), '最多攻击次数，默认 1；不追击，未确认击杀不能宣称死亡。'),
    }, ['entity_id']),
    async execute(ctx, a) {
      const { bot } = ctx, entity = bot.entities[a.entity_id], radius = a.max_distance ?? 3;
      ensure(validEntity(bot, entity, radius), 'ENTITY_MISSING', '目标失效或超出攻击范围。');
      ensure(entity.type !== 'player' && !entity.username && HOSTILE.has(entity.name), 'FORBIDDEN_TARGET', '仅允许攻击白名单中的敌对生物。');
      if (a.weapon) {
        ensure(/_(?:sword|axe)$/.test(a.weapon), 'INVALID_WEAPON', '基础攻击只支持剑或斧。');
        const weapon = itemByName(bot, a.weapon); usable(weapon);
        await ctx.step(() => bot.equip(weapon, 'hand'));
      }
      if (bot.heldItem) usable(bot.heldItem);
      let hurt = false, dead = false, hits = 0;
      const onHurt = (target) => { if (target === entity) hurt = true; };
      const onDead = (target) => { if (target === entity) dead = true; };
      bot.on('entityHurt', onHurt); bot.on('entityDead', onDead);
      ctx.cleanup(() => { bot.removeListener('entityHurt', onHurt); bot.removeListener('entityDead', onDead); });
      for (let i = 0; i < (a.max_hits ?? 1); i++) {
        ctx.check();
        if (dead || !validEntity(bot, entity, radius)) break;
        await ctx.step(() => bot.lookAt(entity.position.offset(0, (entity.height ?? 1) / 2, 0)));
        ensure(validEntity(bot, entity, radius), 'ENTITY_MISSING', '攻击前目标已失效或离开范围。');
        ensure(bot.entityAtCursor(radius) === entity, 'NO_LINE_OF_SIGHT', '目标未处于可见攻击视线内。');
        bot.attack(entity); hits++;
        await ctx.pause(700);
      }
      return state(dead || hurt ? 'completed' : 'uncertain', { entityId: entity.id, hits, damageObserved: hurt, deathObserved: dead, stillVisible: Boolean(validEntity(bot, entity, radius)) });
    }
  });
}
module.exports = registerCombat;
