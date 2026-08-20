"""Neo4j 图谱存储（以 user_id 隔离）。


"""

from collections.abc import Sequence

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.core.config import Settings
from app.schemas import GraphSearchHit
from app.services.query_rewriter import QuerySpec


class Neo4jGraphStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._driver: AsyncDriver | None = None

    def _get_driver(self) -> AsyncDriver:
        if self._driver is None:
            self._driver = AsyncGraphDatabase.driver(
                self._settings.neo4j_uri,
                auth=(self._settings.neo4j_user, self._settings.neo4j_password),
            )
        return self._driver

    async def sync_user_context(
        self,
        user_id: int,
        profile: dict[str, object] | None,
        domain: dict[str, list[dict[str, object]]] | None = None,
    ) -> None:
        """同步用户画像与领域数据到 Neo4j（SoloChef 去家庭化：无日程/成员节点）。"""
        driver = self._get_driver()
        async with driver.session() as session:
            await session.run(
                "MATCH (m:Member {user_id: $user_id}) DETACH DELETE m",
                user_id=user_id,
            )
            await session.run(
                """
                MERGE (u:User {id: $user_id})
                SET u.name = $name, u.goal_type = $goal_type
                WITH u
                FOREACH (preference IN $preferences |
                    MERGE (p:Preference {name: preference})
                    MERGE (u)-[:PREFERS]->(p))
                FOREACH (constraint IN $constraints |
                    MERGE (c:Constraint {name: constraint})
                    MERGE (u)-[:HAS_CONSTRAINT]->(c))
                """,
                user_id=user_id,
                name=(profile or {}).get("display_name", ""),
                goal_type=(profile or {}).get("goal_type", ""),
                preferences=(profile or {}).get("preferences", []),
                constraints=(profile or {}).get("constraints", []),
            )
            if domain:
                await session.run(
                    """
                    MATCH (u:User {id: $user_id})
                    UNWIND $recipes AS recipe
                    MERGE (r:Recipe {user_id: $user_id, id: recipe.id})
                    SET r.name = recipe.name, r.tags = recipe.tags, r.allergens = recipe.allergens
                    MERGE (u)-[:HAS_RECIPE]->(r)
                    FOREACH (ingredient IN recipe.ingredients |
                        MERGE (i:Ingredient {name: ingredient})
                        MERGE (r)-[:REQUIRES]->(i))
                    """,
                    user_id=user_id,
                    recipes=domain.get("recipes", []),
                )
                await session.run(
                    """
                    MATCH (u:User {id: $user_id})
                    UNWIND $tasks AS task
                    MERGE (t:Task {user_id: $user_id, id: task.id})
                    SET t.name = task.title, t.status = task.status, t.category = task.category
                    MERGE (u)-[:HAS_TASK]->(t)
                    """,
                    user_id=user_id,
                    tasks=domain.get("tasks", []),
                )
                await session.run(
                    """
                    MATCH (u:User {id: $user_id})
                    UNWIND $plans AS plan
                    MERGE (p:Plan {user_id: $user_id, id: plan.id})
                    SET p.name = '计划 v' + toString(plan.version), p.summary = plan.summary,
                        p.active = plan.is_active
                    MERGE (u)-[:HAS_PLAN]->(p)
                    WITH u
                    UNWIND $budgets AS budget
                    MERGE (b:Budget {user_id: $user_id, id: budget.id})
                    SET b.name = '预算', b.limit = budget.limit, b.estimated = budget.estimated
                    MERGE (u)-[:HAS_BUDGET]->(b)
                    """,
                    user_id=user_id,
                    plans=domain.get("plans", []),
                    budgets=domain.get("budgets", []),
                )

    async def sync_document_entities(
        self, user_id: int, document_name: str, entities: list[tuple[str, str]]
    ) -> None:
        rows = [{"kind": kind, "name": name} for kind, name in entities]
        async with self._get_driver().session() as session:
            await session.run(
                """
                MERGE (d:Document {user_id: $user_id, name: $document_name})
                WITH d
                UNWIND $entities AS entity
                MERGE (e:KnowledgeEntity {
                    user_id: $user_id,
                    kind: entity.kind,
                    name: entity.name
                })
                MERGE (d)-[:MENTIONS]->(e)
                """,
                user_id=user_id,
                document_name=document_name,
                entities=rows,
            )

    async def sync_document_knowledge(
        self,
        user_id: int,
        document_name: str,
        entities: list[tuple[str, str]],
        relations: list[tuple[str, str, str]] | None = None,
    ) -> None:
        relations = relations or []
        entity_rows = [{"kind": kind, "name": name} for kind, name in entities]
        relation_rows = [
            {"subject": s, "relation": r, "object": o}
            for s, r, o in relations
            if s and r and o
        ]
        async with self._get_driver().session() as session:
            await session.run(
                """
                MERGE (d:Document {user_id: $user_id, name: $document_name})
                WITH d
                UNWIND $entities AS entity
                MERGE (e:KnowledgeEntity {
                    user_id: $user_id,
                    kind: entity.kind,
                    name: entity.name
                })
                MERGE (d)-[:MENTIONS]->(e)
                """,
                user_id=user_id,
                document_name=document_name,
                entities=entity_rows,
            )
            if relation_rows:
                await session.run(
                    """
                    UNWIND $relations AS rel
                    MERGE (s:KnowledgeEntity {user_id: $user_id, name: rel.subject})
                    MERGE (o:KnowledgeEntity {user_id: $user_id, name: rel.object})
                    MERGE (s)-[:RELATION {type: rel.relation, user_id: $user_id}]->(o)
                    """,
                    user_id=user_id,
                    relations=relation_rows,
                )

    async def sync_feedback_signal(
        self,
        user_id: int,
        *,
        signal_key: str,
        feedback_type: str,
        sentiment: str,
        subject: str,
        content: str,
        rating: int | None = None,
        deviation: float = 0.0,
        tags: Sequence[str] = (),
        occurred_at: str = "",
    ) -> None:
        """把一条执行反馈写入图谱，形成可被检索的反馈子图。

        反馈统一挂到 ``:User``：

        ``(:User)-[:HAS_FEEDBACK]->(:FeedbackSignal)-[:ABOUT]->(:KnowledgeEntity)``
        """
        polarity = 1 if sentiment == "positive" else -1 if sentiment == "negative" else 0
        tag_rows = [tag for tag in dict.fromkeys(tags) if tag]
        async with self._get_driver().session() as session:
            await session.run(
                """
                MERGE (u:User {id: $user_id})
                MERGE (fb:FeedbackSignal {user_id: $user_id, key: $signal_key})
                SET fb.name = $subject,
                    fb.type = $feedback_type,
                    fb.sentiment = $sentiment,
                    fb.polarity = $polarity,
                    fb.content = $content,
                    fb.rating = $rating,
                    fb.deviation = $deviation,
                    fb.occurred_at = $occurred_at
                MERGE (u)-[:HAS_FEEDBACK]->(fb)
                WITH fb
                FOREACH (_ IN CASE WHEN $subject = '' THEN [] ELSE [1] END |
                    MERGE (s:KnowledgeEntity {
                        user_id: $user_id, kind: $reference_kind, name: $subject
                    })
                    MERGE (fb)-[:ABOUT]->(s))
                """,
                user_id=user_id,
                signal_key=signal_key,
                subject=subject,
                feedback_type=feedback_type,
                reference_kind=f"{feedback_type}反馈对象",
                sentiment=sentiment,
                polarity=polarity,
                content=content[:1000],
                rating=rating,
                deviation=deviation,
                occurred_at=occurred_at,
            )
            if tag_rows and polarity != 0:
                await session.run(
                    """
                    MATCH (fb:FeedbackSignal {user_id: $user_id, key: $signal_key})
                    UNWIND $tags AS tag
                    MERGE (p:Preference {name: tag})
                    MERGE (fb)-[r:SIGNALS]->(p)
                    SET r.polarity = $polarity, r.user_id = $user_id
                    """,
                    user_id=user_id,
                    signal_key=signal_key,
                    tags=tag_rows,
                    polarity=polarity,
                )

    async def feedback_summary(
        self, user_id: int, feedback_type: str = "", limit: int = 20
    ) -> list[dict[str, object]]:
        """读取用户最近的反馈信号。"""
        async with self._get_driver().session() as session:
            result = await session.run(
                """
                MATCH (:User {id: $user_id})-[:HAS_FEEDBACK]->(fb:FeedbackSignal)
                WHERE $feedback_type = '' OR fb.type = $feedback_type
                RETURN fb.name AS subject, fb.type AS type, fb.sentiment AS sentiment,
                       fb.polarity AS polarity, fb.content AS content,
                       fb.occurred_at AS occurred_at
                ORDER BY fb.occurred_at DESC
                LIMIT $limit
                """,
                user_id=user_id,
                feedback_type=feedback_type,
                limit=limit,
            )
            return list(await result.data())

    async def sync_ingredient_substitutions(
        self, pairs: Sequence[dict[str, object]]
    ) -> int:
        """把显式替代关系写入图谱：``(:Ingredient)-[:SUBSTITUTABLE_FOR {reason, similarity}]->(:Ingredient)``。

        全局节点（不带 ``user_id``），因为食材替代关系是领域常识而非用户私有数据。
        每对写入正反两条边，便于双向检索。返回写入的边数。

        Args:
            pairs: 替代对列表，每项含 ``source`` / ``target`` / ``reason`` / ``similarity``。

        Returns:
            成功写入的边数（双向计数）。
        """
        if not pairs:
            return 0
        rows = [
            {
                "source": str(pair["source"]),
                "target": str(pair["target"]),
                "reason": str(pair.get("reason", "")),
                "similarity": float(pair.get("similarity", 0.8)),
            }
            for pair in pairs
        ]
        async with self._get_driver().session() as session:
            result = await session.run(
                """
                UNWIND $rows AS row
                MERGE (s:Ingredient {name: row.source})
                MERGE (t:Ingredient {name: row.target})
                MERGE (s)-[r:SUBSTITUTABLE_FOR]->(t)
                SET r.reason = row.reason, r.similarity = row.similarity
                MERGE (t)-[r2:SUBSTITUTABLE_FOR]->(s)
                SET r2.reason = row.reason, r2.similarity = row.similarity
                RETURN count(r) AS edges
                """,
                rows=rows,
            )
            record = await result.single()
        return int(record["edges"]) if record else 0

    async def find_substitutions(
        self, ingredient_name: str, limit: int = 5
    ) -> list[dict[str, object]]:
        """按食材名查询图中的显式替代关系。

        匹配策略：先精确等值，再退化为包含匹配（如"牛腩"匹配"牛肉"）。
        返回 ``[{name, reason, similarity}]`` 列表，按相似度降序。

        Args:
            ingredient_name: 购物项名称（可能是"番茄 2 个"等带量描述）。
            limit: 最多返回的替代数。

        Returns:
            替代建议列表，无命中时返回空列表。
        """
        async with self._get_driver().session() as session:
            # 先精确等值匹配（reason/similarity 在关系 r 上）
            result = await session.run(
                """
                MATCH (s:Ingredient {name: $name})-[r:SUBSTITUTABLE_FOR]->(t:Ingredient)
                RETURN t.name AS name, coalesce(r.reason, '') AS reason,
                       coalesce(r.similarity, 0.8) AS similarity
                ORDER BY r.similarity DESC, t.name
                LIMIT $limit
                """,
                name=ingredient_name.strip(),
                limit=limit,
            )
            records = list(await result.data())
            if records:
                return records
            # 退化：包含匹配（处理"番茄 2 个"等带量描述）
            result = await session.run(
                """
                MATCH (s:Ingredient)-[r:SUBSTITUTABLE_FOR]->(t:Ingredient)
                WHERE s.name CONTAINS $keyword
                RETURN t.name AS name, coalesce(r.reason, '') AS reason,
                       coalesce(r.similarity, 0.8) AS similarity
                ORDER BY r.similarity DESC, t.name
                LIMIT $limit
                """,
                keyword=ingredient_name.strip(),
                limit=limit,
            )
            return list(await result.data())

    async def list_document_names(self, user_id: int) -> list[str]:
        """返回用户在 Neo4j 中已有的 Document 节点名称。"""
        async with self._get_driver().session() as session:
            result = await session.run(
                "MATCH (d:Document {user_id: $user_id}) RETURN d.name AS name",
                user_id=user_id,
            )
            records = await result.data()
        return [str(record["name"]) for record in records]

    async def count_entities(self, user_id: int) -> int:
        """返回用户 KnowledgeEntity 节点数量。"""
        async with self._get_driver().session() as session:
            result = await session.run(
                "MATCH (e:KnowledgeEntity {user_id: $user_id}) RETURN count(e) AS n",
                user_id=user_id,
            )
            record = await result.single()
        return int(record["n"]) if record else 0

    async def search(
        self, user_id: int, query: str, query_spec: QuerySpec | None = None
    ) -> list[GraphSearchHit]:
        driver = self._get_driver()
        spec = query_spec or QuerySpec(keywords=[], entity_kinds=[], relations=[])
        cypher = """
        MATCH (source {user_id: $user_id})-[r]->(target)
        WHERE source:User OR source:Recipe OR source:Document
          AND (
            $search_text = ''
            OR toLower(coalesce(source.name, source.title, '')) CONTAINS toLower($search_text)
            OR toLower(coalesce(target.name, target.title, '')) CONTAINS toLower($search_text)
            OR size($keywords) > 0 AND ANY(
                k IN $keywords WHERE
                toLower(coalesce(source.name, source.title, '')) CONTAINS toLower(k)
                OR toLower(coalesce(target.name, target.title, '')) CONTAINS toLower(k)
            )
          )
          AND (
            size($entity_kinds) = 0
            OR ANY(l IN labels(source) WHERE l IN $entity_kinds)
            OR ANY(l IN labels(target) WHERE l IN $entity_kinds)
          )
          AND (
            size($relations) = 0
            OR type(r) IN $relations
            OR type(r) IN ['HAS_CONSTRAINT', 'AVOIDS']
          )
        RETURN coalesce(source.name, source.title, '用户') AS subject,
               type(r) AS relation,
               coalesce(target.name, target.title, '') AS target,
               coalesce(target.day, '') + ' ' + coalesce(target.time, '') AS detail
        ORDER BY relation, subject
        LIMIT 40
        """
        feedback_cypher = """
        MATCH (:User {id: $user_id})-[:HAS_FEEDBACK]->(fb:FeedbackSignal)
        WHERE $search_text = ''
           OR toLower(coalesce(fb.name, '')) CONTAINS toLower($search_text)
           OR toLower(coalesce(fb.content, '')) CONTAINS toLower($search_text)
           OR ANY(k IN $keywords WHERE
                toLower(coalesce(fb.name, '')) CONTAINS toLower(k)
                OR toLower(coalesce(fb.content, '')) CONTAINS toLower(k))
        RETURN coalesce(fb.name, '执行反馈') AS subject,
               'FEEDBACK_' + toUpper(coalesce(fb.sentiment, 'neutral')) AS relation,
               coalesce(fb.content, '') AS target,
               coalesce(fb.type, '') + ' ' + coalesce(fb.occurred_at, '') AS detail
        ORDER BY fb.occurred_at DESC
        LIMIT 8
        """
        async with driver.session() as session:
            result = await session.run(
                cypher,
                user_id=user_id,
                search_text=query.strip(),
                keywords=spec.keywords,
                entity_kinds=spec.entity_kinds,
                relations=spec.relations,
            )
            records = await result.data()
            feedback_records: list[dict[str, object]] = []
            try:
                feedback_result = await session.run(
                    feedback_cypher,
                    user_id=user_id,
                    search_text=query.strip(),
                    keywords=spec.keywords,
                )
                feedback_records = list(await feedback_result.data())
            except Exception:  # noqa: BLE001
                feedback_records = []
        return [GraphSearchHit(**record) for record in (*feedback_records, *records)]

    async def verify(self) -> None:
        await self._get_driver().verify_connectivity()

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
