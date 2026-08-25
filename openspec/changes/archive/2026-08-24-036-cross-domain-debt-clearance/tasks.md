# 036 cross-domain-debt-clearance 实施任务

## 1. agent 面:multimodal 三项(测试先行)

- [x] 1.1 失败测试:③描述段包裹标记与围栏断言;④纯图片问题分流
  (词表命中/未命中双形态)+doc_answer 空 hints 直答;⑦模型上限
  闸(超限 AttachmentError)
- [x] 1.2 ③multimodal.py 描述段包裹(不可信标记+三引号围栏)
- [x] 1.3 ④SQL 意图词表纯函数+graph 分流条件(附件在场且未命中
  →doc_answer)+doc_answer 空 hints 直答形态
- [x] 1.4 ⑦registry 条目 max_image_mb 解析+vision 前体积闸

## 2. api 面:cron 重写(测试先行:安全/性能类)

- [x] 2.1 失败测试:a/b 形态(5/10 序列)/远未来毫秒级(「0 0 1 1 *」
  性能断言)/月末闰年边界/双非*拒既定零回归
- [x] 2.2 parse_cron 扩 a/b 形态+next_fire 日历跳跃重写(trigger
  models 纯函数)

## 3. api 面:附件清理/team_id 回传/收藏原子化(测试先行)

- [x] 3.1 失败测试:孤儿清理(超期 UPLOADED 行+文件双删,新附件
  不动);BotOut/MemoryItemOut 含 team_id;toggle 单语句(并发双击
  响应=终态)
- [x] 3.2 孤儿附件 lifespan 薄壳(每日,RETURNING file_path 删文件)
- [x] 3.3 BotOut/MemoryItemOut 纯增 team_id(响应模型精确化)
- [x] 3.4 toggle_favorite 单语句 CTE 原子化

## 4. web 面:徽标与排除(测试先行不适用,UI 冒烟验证)

- [x] 4.1 BotsPage/MemoryPage 归属徽标(team_id→团队名映射)
- [x] 4.2 TeamsPage 成员候选排除改 user_id

## 5. 验证与收尾

- [x] 5.1 make check 全绿;make contracts;016 评估 smoke 复验零劣化
- [x] 5.2 真冒烟:纯图片问题直答(真 qwen-vl)/带库词走 SQL/描述段
  包裹直证/cron 5/10 保存与远未来即时/孤儿清理手动触发/徽标渲染/
  收藏双击
- [x] 5.3 R1 独立评审+修复清零(0 严重/0 必修/0 建议,见 036 切口清单 a–h 全覆盖)
- [x] 5.4 HANDOFF+挂账(词表启发式误判面/清理竞态注记/文件残留运维面)+归档
