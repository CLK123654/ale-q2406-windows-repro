from __future__ import annotations
import csv,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent; TASK=ROOT/"task"
def read(name): return (TASK/name).read_text(encoding="utf-8").rstrip("\n")
fields={"任务名称":read("任务名称.txt"),"学科一级分类":"Computing, Data & Mathematical Sciences 计算、数据与数学科学","学科二级分类":"数据与分析工程","题目难度":"高","任务概要":read("任务概要.txt"),"涉及的专业软件":"PostgreSQL Client","包含的操作类型":["领域专业软件操作","文件和数据处理","脚本/命令/程序化处理","结果验证"],"任务prompt‼️":read("任务prompt.txt"),"关键动作":read("关键动作.txt"),"评分表（直接输入内容或使用txt文档）‼️":read("评分表.txt"),"本项任务的环境依赖":read("环境依赖.txt"),"相关专业软件的关键步骤":read("相关专业软件的关键步骤.txt"),"是否支持Windows":"是"}
(TASK/"task_fields.json").write_text(json.dumps(fields,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
headers=["题目ID","学科一级分类","学科二级分类","题目难度","任务概要","涉及的专业软件","包含的操作类型","任务prompt‼️","输入数据包（压缩包）‼️","reference（压缩包）‼️","关键标准答案‼️","关键动作","评分表（直接输入内容或使用txt文档）‼️","本项任务的环境依赖","相关专业软件的关键步骤","任务规格转化（excel或txt文档）","初标是否已完成","生产专家","完成日期"]
row={"题目ID":"","学科一级分类":fields["学科一级分类"],"学科二级分类":fields["学科二级分类"],"题目难度":fields["题目难度"],"任务概要":fields["任务概要"],"涉及的专业软件":fields["涉及的专业软件"],"包含的操作类型":"、".join(fields["包含的操作类型"]),"任务prompt‼️":fields["任务prompt‼️"],"输入数据包（压缩包）‼️":"输入数据包.zip","reference（压缩包）‼️":"reference.zip","关键标准答案‼️":"关键标准答案.xlsx","关键动作":fields["关键动作"],"评分表（直接输入内容或使用txt文档）‼️":fields["评分表（直接输入内容或使用txt文档）‼️"],"本项任务的环境依赖":fields["本项任务的环境依赖"],"相关专业软件的关键步骤":fields["相关专业软件的关键步骤"],"任务规格转化（excel或txt文档）":"任务规格转化.xlsx","初标是否已完成":"是","生产专家":"用户883679","完成日期":"2026-08-12"}
with (TASK/"ALE-专家数据作业表_q2406.csv").open("w",encoding="utf-8-sig",newline="") as h:w=csv.DictWriter(h,fieldnames=headers,lineterminator="\n");w.writeheader();w.writerow(row)

