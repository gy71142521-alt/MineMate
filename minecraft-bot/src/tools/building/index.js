const { object, text, position } = require('../schema');
const { ensure, blockAt, vector, itemByName, state } = require('../common');
const FACES = Object.freeze({ up: [0, 1, 0], down: [0, -1, 0], north: [0, 0, -1], south: [0, 0, 1], east: [1, 0, 0], west: [-1, 0, 0] });
const parameters = object({ block_name: text, position, reference_position: position, face: { type: 'string', enum: Object.keys(FACES) } });
function inspect(ctx, a) {
  ensure(ctx.bot.registry.blocksByName[a.block_name], 'UNKNOWN_BLOCK', '指定名称不是当前版本的方块。');
  const target = blockAt(ctx, a.position);
  const reference = blockAt(ctx, a.reference_position);
  const [x, y, z] = FACES[a.face];
  const direction = vector({ x, y, z });
  ensure(reference.position.plus(direction).equals(vector(a.position)), 'INVALID_FACE', '目标必须位于参照方块所选面的相邻位置。');
  ensure(['air', 'cave_air', 'void_air'].includes(target.name), 'OCCUPIED', '目标位置已被占用，不会覆盖现有方块。');
  ensure(reference.boundingBox === 'block', 'INVALID_REFERENCE', '参照方块必须提供可靠的实体支撑。');
  ensure(!/chest|barrel|furnace|crafting_table|shulker|door|button|lever|bed$/.test(reference.name), 'INTERACTIVE_REFERENCE', '参照方块会触发交互，请选择普通实体方块。');
  for (const entity of Object.values(ctx.bot.entities ?? {})) {
    if (!entity?.isValid || !entity.position) continue;
    const p = entity.position;
    ensure(!(Math.abs(p.x - (a.position.x + 0.5)) < 0.8 && Math.abs(p.z - (a.position.z + 0.5)) < 0.8 && p.y < a.position.y + 1 && p.y + (entity.height ?? 1.8) > a.position.y), 'ENTITY_COLLISION', '目标位置可能与实体碰撞。');
  }
  return { reference, direction };
}
function registerBuilding(registry) {
  registry.register({ name: 'inspect_placement', description: '只读检查单个方块位置、支撑面与实体碰撞，不放置。', parameters, mutates: false,
    execute(ctx, a) { inspect(ctx, a); return state('completed', { placeable: true, position: a.position }); } });
  registry.register({ name: 'place_block', description: '在明确坐标放置单个方块，不能覆盖方块或实体。', parameters,
    async execute(ctx, a) {
      inspect(ctx, a);
      const item = itemByName(ctx.bot, a.block_name);
      await ctx.step(() => ctx.bot.equip(item, 'hand'));
      const { reference, direction } = inspect(ctx, a);
      await ctx.step(() => ctx.bot.placeBlock(reference, direction));
      const actual = ctx.bot.blockAt(vector(a.position));
      return state(actual?.name === a.block_name ? 'completed' : 'uncertain', { position: a.position, actualBlock: actual?.name ?? null });
    }
  });
}
module.exports = registerBuilding;
