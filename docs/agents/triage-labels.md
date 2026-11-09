# Triage labels

规范角色与本地实施 issue 的 `Status:` 取值对应如下：

| 规范角色 | 本地 Status 值 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 等待维护者评估 |
| `needs-info` | `needs-info` | 等待补充信息 |
| `ready-for-agent` | `ready-for-agent` | 需求明确，可交给 agent 实施 |
| `ready-for-human` | `ready-for-human` | 需要人工实施 |
| `wontfix` | `wontfix` | 已决定不处理 |

技能要求应用某个 triage 角色时，将实施 issue 的 `Status:` 设置为对应值。调整标签名称时，修改本表“本地 Status 值”列。
