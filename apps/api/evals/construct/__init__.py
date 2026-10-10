"""评测集自动构造(092):生成-自洽验证-人审三段闸的 harness 侧管线。

validators=README 编写规范操作化(纯函数,与手工新题共用);generate=LLM
候选生成+自洽验证(gold 直连/规范/去重);merge=人审并入收口(移动+全量
重跑+基线行登记)。候选落 datasets/candidates/<tenant>/(文件态,不落库)。
"""
