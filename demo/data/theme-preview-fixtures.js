// 独立风格预览专用虚构数据；不读取账号、商品快照或业务文件。
export const THEMES = [
  { id: 'A', name: '图片货架', subtitle: '看图挑选，草稿随收随放', source: 'Shadcn Admin', description: '横向切换账号，浏览图片货架，在底部展开今日草稿。' },
  { id: 'B', name: '表格批量', subtitle: '整表比较，批量加入草稿', source: 'Ant', description: '窄侧导航与全宽数据表，勾选多件后一次加入草稿。' },
  { id: 'C', name: '上下工作区', subtitle: '上方找商品，下方核对草稿', source: 'Tabler', description: '左侧固定账号，上下同时查看商品与草稿，可调整工作区比例。' },
  { id: 'D', name: '分步向导', subtitle: '选账号、挑商品、核对草稿', source: 'Fluent', description: '一次专注一个步骤，完成账号选择后挑选商品，再核对草稿。' },
  { id: 'E', name: '快速检视', subtitle: '左侧浏览，右侧查看详情', source: 'Carbon', description: '深色导航，左侧商品索引与右侧详情联动，逐件快速检视。' },
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
