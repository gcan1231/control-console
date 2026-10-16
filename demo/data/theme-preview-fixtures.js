// 独立风格预览专用虚构数据；不读取账号、商品快照或业务文件。
export const THEMES = [
  { id: 'A', name: '石墨极简', subtitle: '清晰克制 · 聚焦任务', source: 'Shadcn Admin', description: '石墨色主操作、细边框与清晰表格层级。' },
  { id: 'B', name: '蓝白专业', subtitle: '明确反馈 · 稳定秩序', source: 'Ant', description: '蓝色主操作、紧凑分组与明确的状态反馈。' },
  { id: 'C', name: '青绿轻量', subtitle: '轻盈留白 · 快速浏览', source: 'Tabler', description: '青绿色强调、轻量卡片与易浏览的信息密度。' },
  { id: 'D', name: '靛蓝柔和', subtitle: '柔和层级 · 连贯操作', source: 'Fluent', description: '靛蓝色强调、柔和圆角与连贯的操作区域。' },
  { id: 'E', name: '工业秩序', subtitle: '严谨网格 · 直接表达', source: 'Carbon', description: '方正网格、高对比信息与直接的操作表达。' },
];

export const ACCOUNTS = [
  { id: 'demo-food', name: '演示食光小铺', category: '食品/宠物', operator: '演示运营甲', target: 5, number: 'DEMO01' },
  { id: 'demo-home', name: '演示生活选物', category: '百货/个护家清', operator: '演示运营乙', target: 4, number: 'DEMO02' },
  { id: 'demo-baby', name: '演示成长日记', category: '母婴', operator: '演示运营甲', target: 3, number: 'DEMO03' },
];

const productGroups = [
  { category: '食品/宠物', art: 'snack', names: [
    '演示烘焙燕麦脆', '演示混合坚果包', '演示冻干草莓粒', '演示海苔小方片',
    '演示低糖米饼', '演示猫咪磨牙饼', '演示香烤南瓜籽', '演示果仁能量棒',
    '演示紫薯小圆饼', '演示宠物冻干粒', '演示黑芝麻薄脆', '演示山楂果条',
  ] },
  { category: '百货/个护家清', art: 'bottle', names: [
    '演示温和洗手液', '演示厨房清洁泡沫', '演示便携收纳盒', '演示柔软擦拭布',
    '演示衣物护理液', '演示旅行分装瓶', '演示柑橘洗洁精', '演示桌面收纳篮',
    '演示玻璃清洁喷雾', '演示浴室置物架', '演示绵柔洗脸巾', '演示鞋面清洁慕斯',
  ] },
  { category: '母婴', art: 'box', names: [
    '演示婴童棉柔巾', '演示宝宝硅胶餐盘', '演示儿童围兜', '演示婴童收纳袋',
    '演示宝宝学饮杯', '演示儿童洗浴手套', '演示婴童口水巾', '演示宝宝软头勺',
    '演示儿童防水餐垫', '演示婴童纱布浴巾', '演示宝宝餐具盒', '演示儿童洗手泡泡',
  ] },
];

export const PRODUCTS = productGroups.flatMap((group, groupIndex) =>
  group.names.map((name, index) => ({
    // 使用字符串拼接，避免19位ID经过浮点数而丢失精度。
    id: `900000000000000${String(groupIndex * 12 + index + 1).padStart(4, '0')}`,
    name,
    category: group.category,
    date: index < 9 ? '2026-10-16' : '2026-10-15',
    commission: 18 + ((index * 3 + groupIndex * 2) % 23),
    totalSales: 1600 + groupIndex * 730 + index * 457,
    dailySales: 48 + ((index * 37 + groupIndex * 19) % 320),
    history: index % 3 === 0 ? ['2026-10-14'] : [],
    art: group.art,
    shop: `演示${['食光', '生活', '成长'][groupIndex]}商店${index % 3 + 1}`,
    group: `演示${['食品宠物', '百货家清', '母婴'][groupIndex]}协同群`,
  })),
);

export const HEADERS = [
  '日期', '运营人', '抖音名称', '抖音账号', '作品描述/商品短标题', '商品ID',
  '企微群名称', '商品链接', '账号合作码/uid', '视频链接', '店铺名称',
  '剪辑软件日志', '发文软件日志', '千川塞素材软件日志', '检查软件日志', '企微同步软件日志',
];
