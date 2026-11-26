const { ensure } = require('../common');

// 资源名称只从当前连接的 Mineflayer registry 读取。
// 不维护静态物品别名表，因此也能发现模组在运行时注册的资源。
function normalize(value) {
  return String(value ?? '')
    .trim()
    .toLocaleLowerCase()
    .replace(/^minecraft:/, '')
    .replace(/[^\p{L}\p{N}]+/gu, '');
}

function collectEntries(registry, kind) {
  const groups = kind === 'item'
    ? [['item', registry?.itemsByName]]
    : kind === 'block'
      ? [['block', registry?.blocksByName]]
      : kind === 'entity'
        ? [['entity', registry?.entitiesByName]]
        : [['item', registry?.itemsByName], ['block', registry?.blocksByName]];
  const entries = [];
  for (const [entryKind, table] of groups) {
    if (!table || typeof table !== 'object') continue;
    for (const [key, value] of Object.entries(table)) {
      if (!value || typeof value !== 'object') continue;
      const name = typeof value.name === 'string' ? value.name : key;
      if (typeof name !== 'string' || !/^[a-z0-9_.:/-]+$/i.test(name)) continue;
      entries.push({
        kind: entryKind,
        name: name.replace(/^minecraft:/i, ''),
        displayName: typeof value.displayName === 'string' ? value.displayName : name,
        id: Number.isInteger(value.id) ? value.id : null,
        stackSize: Number.isInteger(value.stackSize) ? value.stackSize : null
      });
    }
  }
  return entries;
}

function scoreEntry(entry, query) {
  const q = normalize(query);
  const name = normalize(entry.name);
  const display = normalize(entry.displayName);
  if (!q) return 0;
  if (q === name) return 1;
  if (q === display) return 0.98;
  if (name.startsWith(q)) return 0.85;
  if (display.startsWith(q)) return 0.82;
  if (name.includes(q)) return 0.72;
  if (display.includes(q)) return 0.7;
  return 0;
}

function resolveResource(bot, args) {
  const query = typeof args?.query === 'string' ? args.query.trim() : '';
  const kind = args?.kind ?? 'any';
  ensure(query.length > 0 && query.length <= 64 && !/[\u0000-\u001f\u007f]/.test(query), 'INVALID_ARGUMENT', '资源查询词无效。');
  ensure(['item', 'block', 'entity', 'any'].includes(kind), 'INVALID_ARGUMENT', '资源类型无效。');
  const scored = collectEntries(bot?.registry, kind)
    .map((entry) => ({ entry, confidence: scoreEntry(entry, query) }))
    .filter((item) => item.confidence > 0)
    .sort((a, b) => b.confidence - a.confidence || a.entry.name.localeCompare(b.entry.name));
  const candidates = scored.slice(0, 16).map(({ entry, confidence }) => ({ ...entry, confidence }));
  const top = scored[0]?.confidence ?? 0;
  return {
    status: 'completed',
    available: true,
    source: 'runtime_registry',
    query,
    kind,
    candidates,
    ambiguous: candidates.length > 1 && candidates[1].confidence === top,
    truncated: scored.length > candidates.length,
    registryVersion: bot?.version ?? bot?.protocolVersion ?? null
  };
}

function registerResourceTools(registry) {
  registry.register({
    name: 'resolve_resource',
    description: '只读查询当前 Minecraft 运行时注册表中的物品、方块或实体类型候选。kind=entity 查询实体；any 保持仅物品和方块。必须先确认目标的真实 name；无法唯一确定时应向玩家澄清。',
    parameters: {
      type: 'object',
      properties: {
        query: { type: 'string', minLength: 1, maxLength: 64, description: '从玩家原话提取的目标名称或短语，例如 橡木、cow、iron_ore。' },
        kind: { type: 'string', enum: ['item', 'block', 'entity', 'any'], description: '要解析的资源类型：item 物品、block 方块、entity 实体、any 仅物品和方块。' }
      },
      required: ['query', 'kind'],
      additionalProperties: false
    },
    mutates: false,
    execute: ({ bot }, args) => resolveResource(bot, args)
  });
}

module.exports = { collectEntries, normalize, resolveResource, registerResourceTools };
