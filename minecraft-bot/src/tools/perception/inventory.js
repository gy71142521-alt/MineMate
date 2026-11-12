const EQUIPMENT_SLOTS = Object.freeze({
  5: ['mainhand', 'feet', 'legs', 'chest', 'head'],
  6: ['mainhand', 'offhand', 'feet', 'legs', 'chest', 'head']
});

function copyItem(item, slot) {
  if (!item || typeof item.name !== 'string' || !Number.isFinite(item.count) || !Number.isFinite(slot)) return null;
  return { name: item.name, count: item.count, slot };
}

function readInventory(bot, ready) {
  if (!ready || !Array.isArray(bot.inventory?.slots)) {
    return { available: false, items: [], equipment: [] };
  }

  const items = [];
  for (let slot = 0; slot < bot.inventory.slots.length; slot += 1) {
    const item = copyItem(bot.inventory.slots[slot], slot);
    if (item) items.push(item);
  }

  const equipment = [];
  const slots = bot.entity?.equipment;
  const labels = Array.isArray(slots) ? EQUIPMENT_SLOTS[slots.length] : null;
  if (labels) {
    slots.forEach((value, index) => {
      const item = copyItem(value, index);
      if (item) equipment.push({ name: item.name, count: item.count, slot: labels[index] });
    });
  }

  return { available: true, items, equipment };
}

function countItem(inventory, name) {
  if (!inventory.available || typeof name !== 'string' || !name) return null;
  const key = name.toLowerCase();
  return inventory.items.reduce((total, item) => total + (item.name.toLowerCase() === key ? item.count : 0), 0);
}

module.exports = { readInventory, countItem };
