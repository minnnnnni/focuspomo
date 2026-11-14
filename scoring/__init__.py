"""집중도 점수 패키지 (개발계획서 2.3·2.5, 집중도 점수 산출 명세).

- focus_score.py: 모드·태그·점수·집계 (SCORE-01, 김민서). LLM 없이 결정론적으로 계산
- llm_client.py: OpenAI 호환 LLM 클라이언트 (Gemini 기본·Groq), 재시도 (SCORE-03, 신명철)
- llm_classify.py: 고유 창 목록 LLM 분류 (SCORE-02, 신명철)
- report_text.py: 리포트 문구 — LLM 또는 템플릿 (SCORE-02·03, 신명철)
- scorer.py: LlmScorer — 분류 → 점수 → 문구를 잇는 실제 점수 함수 (INT-05)

PySide6를 import하지 않는다 (GUI 없이 테스트).
"""
