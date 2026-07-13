# ADR-0001：采用模块化单体与独立 Worker 进程

- Status: Accepted
- Date: 2026-07-13

## Context

首轮原型是 Windows 本地、单用户、30—50 章。长时间模型调用不能阻塞 API 请求，但没有证据表明需要 Kubernetes、消息队列集群或多个独立服务。

## Decision

- Backend 使用一个 Python 领域包。
- API 与 Worker 作为两个进程运行。
- 二者共享 SQLite、领域模型、Repository 和 Artifact Store。
- 领域模块之间通过显式包边界和 Port 隔离，而非网络微服务。

## Consequences

- 本地部署和调试简单。
- TaskRunner、Repository、Provider、GraphStore 仍保留可替换接口。
- 若未来出现多人并发或大规模队列需求，可替换基础设施，不改变核心对象所有权。
