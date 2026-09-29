# FocusPomo Git 협업 가이드

> Git이 처음이어도 이 문서만 보고 따라 할 수 있도록 정리한 팀 참고 문서입니다.
> 개발 환경 설정(Python, venv 등)은 `개발환경_설정가이드.md`를 먼저 끝내 주세요.
> 모든 명령은 **Windows PowerShell**, 레포 폴더(`C:\dev\focuspomo`) 기준입니다.

---

## 0. 핵심 규칙 3가지

1. **`main`에서 직접 작업하지 않는다.** 모든 작업은 `feature/...` 브랜치에서 한다.
2. **작업 시작 전에 항상 `main`을 최신으로 받는다.** (`git pull`)
3. **`main`에 들어가는 코드는 반드시 Pull Request(PR)를 거친다.** 팀원 1명 이상이 확인 후 merge.

---

## 1. 브랜치 구조

```
main  ──●────────●────────────●──────────●──▶   (항상 실행 가능한 상태 유지)
         \      /  \          /  \        /
          ●──●─●    ●──●──●──●    ●──●───●
   feature/cv-ear   feature/gui-timer   feature/window-sampler
```

| 브랜치                  | 용도                                         | 누가              |
| ----------------------- | -------------------------------------------- | ----------------- |
| `main`                  | 통합된 최종 코드. 항상 실행 가능한 상태 유지 | PR merge로만 변경 |
| `feature/<모듈>-<작업>` | 기능 하나를 개발하는 작업용 브랜치           | 각자              |
| `fix/<모듈>-<내용>`     | 버그 수정                                    | 각자              |
| `docs/<내용>`           | 문서만 수정할 때                             | 각자              |

### 브랜치 이름 규칙

- 소문자 영어 + 하이픈(`-`)만 사용 (한글·공백 금지)
- 앞에 **모듈 이름**을 붙여 누구 작업인지 바로 알 수 있게 합니다.

| 담당   | 모듈 접두어              | 예시                                                                       |
| ------ | ------------------------ | -------------------------------------------------------------------------- |
| 김시언 | `cv`                     | `feature/cv-ear-calc`, `feature/cv-calibration`, `fix/cv-blink-count`      |
| 신명철 | `window`, `score`, `agg` | `feature/window-sampler`, `feature/score-llm`, `feature/agg-slot-compress` |
| 김민서 | `gui`, `storage`         | `feature/gui-timer`, `feature/gui-report-view`, `feature/storage-jsonl`    |

> 💡 브랜치 하나 = 작업 하나. 크기는 **며칠 안에 끝낼 수 있는 정도**가 좋습니다. 브랜치가 오래 살아 있을수록 `main`과 멀어져 충돌이 커집니다.

---

## 2. 최초 설정 (1회만)

### 2-1. 사용자 정보와 기본 옵션

```powershell
git config --global user.name "홍길동"
git config --global user.email "your-email@example.com"   # GitHub 계정 이메일
git config --global core.autocrlf true                    # Windows 줄바꿈 처리
git config --global init.defaultBranch main
git config --global pull.rebase false                     # pull 시 merge 방식 사용 (초보자에게 안전)
```

설정 확인:

```powershell
git config --global --list
```

### 2-2. 레포 clone

```powershell
cd C:\dev
git clone https://github.com/<OWNER>/focuspomo.git
cd focuspomo
```

### 2-3. 레포 관리자만: `main` 보호 설정 (1회)

GitHub 레포 → **Settings → Branches → Add branch ruleset**(또는 _Add rule_)에서 `main`에 대해:

- ✅ **Require a pull request before merging** (직접 push 금지)
- ✅ **Require approvals: 1**

이렇게 해 두면 실수로 `main`에 직접 push하는 일이 원천 차단됩니다.

---

## 3. 전체 작업 흐름 한눈에 보기

```
① main 최신화        git switch main → git pull
② 브랜치 만들기      git switch -c feature/xxx
③ 작업 + 커밋        (코드 수정) → git add → git commit   ← 여러 번 반복
④ push               git push -u origin feature/xxx
⑤ PR 생성            GitHub 웹에서 Pull Request 작성
⑥ 리뷰 & merge       팀원 승인 → Squash and merge
⑦ 정리               main으로 돌아와 pull → 로컬 브랜치 삭제
```

아래에서 단계별로 자세히 설명합니다.

---

## 4. 단계별 상세

### ① 작업 시작 전: `main` 최신화 (매번!)

```powershell
cd C:\dev\focuspomo
.\.venv\Scripts\Activate.ps1
git switch main
git pull
pip install -r requirements.txt    # 다른 팀원이 패키지를 추가했을 수 있음
```

`git pull`은 GitHub에 올라간 최신 `main`을 내 컴퓨터로 가져오는 명령입니다. 이걸 건너뛰고 브랜치를 만들면 **옛날 코드 위에서 작업**하게 되어 나중에 충돌이 납니다.

### ② 작업용 브랜치 만들기

```powershell
git switch -c feature/gui-timer
```

`-c`는 "새로 만들고 이동"이라는 뜻입니다. 지금 어떤 브랜치에 있는지 확인하려면:

```powershell
git branch          # * 표시가 현재 브랜치
git status          # 첫 줄에 "On branch feature/gui-timer"
```

### ③ 작업하고 커밋하기

코드를 수정한 뒤:

```powershell
git status                    # 무엇이 바뀌었는지 확인
git diff                      # 바뀐 내용 자세히 보기 (q로 나가기)
git add gui/timer_view.py     # 커밋에 포함할 파일 선택
git commit -m "feat(gui): 25분 타이머 틱 구현"
```

여러 파일을 한꺼번에 담으려면 `git add .` 도 가능하지만, **그 전에 `git status`로 이상한 파일(`.env`, `data/` 등)이 섞여 있지 않은지 꼭 확인**하세요.

#### 커밋 단위

- "하나의 의미 있는 변경 = 커밋 하나"
- 하루 작업을 커밋 하나에 몰아넣지 말고, 기능이 조금씩 동작할 때마다 커밋합니다.
- 동작하지 않는 중간 상태라도 **내 브랜치에서는** 커밋해도 괜찮습니다 (PR 때 정리됨).

#### 커밋 메시지 규칙

```
<종류>(<모듈>): <무엇을 했는지 한 줄 요약>
```

| 종류       | 언제                     | 예시                                            |
| ---------- | ------------------------ | ----------------------------------------------- |
| `feat`     | 새 기능                  | `feat(cv): EAR 기반 1초 요약 생성`              |
| `fix`      | 버그 수정                | `fix(window): 잠금 화면에서 app_name None 처리` |
| `refactor` | 동작 변화 없는 구조 개선 | `refactor(agg): 압축 임계값을 config로 분리`    |
| `test`     | 테스트 추가/수정         | `test(agg): 샘플 누락 슬롯 경계값 테스트`       |
| `docs`     | 문서                     | `docs: Git 협업 가이드 추가`                    |
| `chore`    | 설정, 패키지 등          | `chore: requirements.txt에 pywinauto 추가`      |

메시지는 한국어로 써도 됩니다. ❌ `수정`, `asdf`, `final_real_final` 같은 메시지는 피해 주세요.

### ④ GitHub에 push

처음 push할 때:

```powershell
git push -u origin feature/gui-timer
```

`-u`는 "이 로컬 브랜치를 GitHub의 같은 이름 브랜치와 연결"하는 옵션입니다. 한 번 해 두면 이후에는:

```powershell
git push
```

만 쓰면 됩니다. 작업 도중에도 자주 push해 두면 컴퓨터가 고장 나도 코드가 안전합니다.

### ⑤ Pull Request(PR) 만들기

1. push 직후 GitHub 레포 페이지에 가면 노란 배너로 **"Compare & pull request"** 버튼이 뜹니다. 클릭합니다.
   (안 보이면 **Pull requests** 탭 → **New pull request** → `base: main` ← `compare: feature/gui-timer` 선택)
2. **base가 `main`**인지 확인합니다.
3. 제목과 설명을 작성합니다.

```markdown
## 무엇을 했나요

- 25분/5분/15분 타이머 상태 전환 구현
- mock 리포트 데이터로 휴식 화면 렌더링

## 어떻게 확인하나요

- `python main.py` 실행 → 타이머 시작 → 테스트용으로 줄인 세션 시간(10초) 후 리포트 화면 표시 확인

## 참고 / 논의할 점

- total_score 표시 형식은 아직 원점수로 둠 (개발계획서 8장 열린 질문)
```

4. 오른쪽 **Reviewers**에 팀원을 지정하고 **Create pull request**를 누릅니다.
5. 팀 채널에 PR 링크를 공유합니다.

> 💡 아직 완성 전이지만 먼저 보여주고 싶다면 **Create draft pull request**로 만들 수 있습니다. 완성되면 **Ready for review**를 누르면 됩니다.

### ⑥ 리뷰와 merge

**리뷰하는 사람**

- PR의 **Files changed** 탭에서 변경 내용을 봅니다.
- 궁금한 줄에 마우스를 올려 `+`를 눌러 코멘트를 남깁니다.
- 가능하면 브랜치를 받아서 직접 실행해 봅니다:
  ```powershell
  git fetch
  git switch feature/gui-timer
  ```
- 문제없으면 **Review changes → Approve**.

**PR 작성자**

- 리뷰 코멘트를 반영할 때는 **같은 브랜치에서** 수정 → 커밋 → `git push` 하면 PR에 자동으로 추가됩니다. PR을 새로 만들 필요 없습니다.
- 승인을 받으면 **Squash and merge** 버튼으로 merge합니다.
  (Squash and merge = 브랜치의 여러 커밋을 하나로 합쳐 `main`에 넣음 → `main` 기록이 깔끔해짐. 버튼 옆 ▼에서 선택)
- merge 후 나오는 **Delete branch** 버튼을 눌러 GitHub의 브랜치를 삭제합니다.

### ⑦ 정리

merge가 끝났으면 내 컴퓨터도 정리합니다.

```powershell
git switch main
git pull                              # 방금 merge된 내용 받기
git branch -d feature/gui-timer       # 로컬 브랜치 삭제
git fetch --prune                     # GitHub에서 지워진 브랜치 정보 정리
```

> `git branch -d`가 "not fully merged" 오류를 내는 경우가 있습니다. Squash merge를 하면 Git이 병합 여부를 인식하지 못해서 생기는 정상적인 현상입니다. **GitHub에서 PR이 merge된 것을 확인했다면** 대문자 `-D`로 삭제하면 됩니다.
>
> ```powershell
> git branch -D feature/gui-timer
> ```

그리고 다음 작업은 다시 **①부터** 시작합니다.

---

## 5. 작업 도중 `main`이 바뀌었을 때

내가 브랜치에서 작업하는 동안 다른 팀원의 PR이 `main`에 merge될 수 있습니다. 내 브랜치에 최신 `main`을 반영하려면:

```powershell
git switch main
git pull
git switch feature/gui-timer
git merge main
```

- 충돌이 없으면 자동으로 merge 커밋이 생깁니다 (편집기가 뜨면 저장하고 닫으면 됨). 이후 `git push`.
- **PR을 올리기 직전**에 한 번 해 두면 PR에서 충돌이 날 일이 줄어듭니다.
- 특히 공용 파일(`main.py`, `requirements.txt`, `config/`)을 건드린 경우 자주 해 주세요.

---

## 6. 충돌(conflict) 해결하기

두 사람이 같은 파일의 같은 부분을 고치면 Git이 자동으로 합치지 못하고 충돌을 알립니다.

```
CONFLICT (content): Merge conflict in main.py
Automatic merge failed; fix conflicts and then commit the result.
```

당황하지 말고 다음 순서로 해결합니다.

1. `git status`로 충돌 난 파일을 확인합니다 (`both modified`로 표시됨).
2. 파일을 열면 아래처럼 표시되어 있습니다.
   ```python
   <<<<<<< HEAD
   cv_thread = CVThread(fps=15)          # ← 내 브랜치의 코드
   =======
   cv_thread = CVThread(fps=config.FPS)  # ← main에서 온 코드
   >>>>>>> main
   ```
3. 두 내용을 보고 **최종적으로 남길 코드만 남기고** `<<<<<<<`, `=======`, `>>>>>>>` 표시 줄을 모두 지웁니다.
   (VS Code에서는 충돌 부분 위에 **Accept Current / Accept Incoming / Accept Both** 버튼이 떠서 편합니다.)
4. 저장 후:
   ```powershell
   git add main.py
   git commit
   git push
   ```

> 💡 어느 쪽을 남겨야 할지 모르겠으면 **혼자 결정하지 말고 그 코드를 쓴 팀원에게 물어보세요.**
> 중간에 포기하고 원래대로 돌아가려면 `git merge --abort`.

---

## 7. 자주 하는 실수와 해결

### 7-1. 실수로 `main`에서 작업하고 커밋까지 했다 (아직 push 안 함)

```powershell
git switch -c feature/내작업       # 지금 상태 그대로 새 브랜치로 옮김
git switch main
git reset --hard origin/main       # 로컬 main을 GitHub 상태로 되돌림
git switch feature/내작업          # 다시 작업 브랜치로
```

> ⚠️ `reset --hard`는 되돌릴 수 없습니다. **반드시 첫 줄로 브랜치를 먼저 만든 뒤** 실행하세요.

### 7-2. 브랜치를 안 만들고 `main`에서 코드를 고쳤다 (커밋 전)

수정 내용은 그대로 들고 브랜치만 만들면 됩니다.

```powershell
git switch -c feature/내작업
```

### 7-3. 다른 브랜치로 가야 하는데 아직 커밋하기 애매한 작업이 있다

```powershell
git stash            # 작업 내용을 임시 보관
git switch main
# ... 다른 일 ...
git switch feature/내작업
git stash pop        # 보관했던 작업 다시 꺼내기
```

### 7-4. 방금 한 커밋 메시지를 고치고 싶다 (push 전)

```powershell
git commit --amend -m "feat(gui): 올바른 메시지"
```

### 7-5. 커밋에 빠뜨린 파일이 있다 (push 전)

```powershell
git add 빠뜨린파일.py
git commit --amend --no-edit
```

### 7-6. 방금 한 커밋을 취소하고 싶다 (push 전, 수정 내용은 유지)

```powershell
git reset --soft HEAD~1
```

### 7-7. 커밋하면 안 되는 파일(`.env`, API 키 등)을 올려 버렸다

- **push 전**: 7-6으로 커밋을 취소하고 해당 파일을 빼고 다시 커밋.
- **push 후**: 파일을 지워도 Git 기록에 남습니다. **즉시 팀에 알리고, API 키는 발급처에서 폐기(revoke) 후 새로 발급**하세요. 기록에서 지우는 것보다 키를 폐기하는 게 우선입니다.

### 7-8. `git push`가 거절된다 (`rejected`, `fetch first`)

GitHub의 내 브랜치에 내 컴퓨터에 없는 커밋이 있다는 뜻입니다 (예: 리뷰어가 웹에서 직접 수정).

```powershell
git pull
git push
```

> `main`에 push하려다 거절됐다면 2-3의 보호 규칙 때문입니다. 정상입니다 — 브랜치를 만들어 PR로 올려 주세요 (7-1 참고).

---

## 8. 커밋하면 안 되는 것

`.gitignore`에 이미 등록되어 있지만, `git add` 전에 `git status`로 한 번 더 확인하세요.

| 대상                               | 이유                      |
| ---------------------------------- | ------------------------- |
| `.venv/`                           | 개인 가상환경             |
| `data/`                            | 세션 기록 — 개인정보 성격 |
| `.env`, `config/local_settings.py` | **LLM API 키**            |
| `__pycache__/`                     | 자동 생성 파일            |

---

## 9. 명령어 치트시트

| 하고 싶은 일                | 명령                               |
| --------------------------- | ---------------------------------- |
| 현재 상태 보기              | `git status`                       |
| 현재 브랜치 / 브랜치 목록   | `git branch`                       |
| 변경 내용 보기              | `git diff`                         |
| 최근 커밋 기록 보기         | `git log --oneline --graph -10`    |
| main 최신화                 | `git switch main` → `git pull`     |
| 새 브랜치 만들고 이동       | `git switch -c feature/xxx`        |
| 기존 브랜치로 이동          | `git switch feature/xxx`           |
| 파일 스테이징               | `git add 파일` / `git add .`       |
| 커밋                        | `git commit -m "feat(모듈): 내용"` |
| 첫 push                     | `git push -u origin feature/xxx`   |
| 이후 push                   | `git push`                         |
| 내 브랜치에 main 반영       | `git merge main`                   |
| 임시 보관 / 꺼내기          | `git stash` / `git stash pop`      |
| 로컬 브랜치 삭제            | `git branch -d feature/xxx`        |
| 원격에서 지워진 브랜치 정리 | `git fetch --prune`                |

---

## 10. 하루 작업 루틴 요약

```powershell
# ── 아침: 작업 시작 ──
cd C:\dev\focuspomo
.\.venv\Scripts\Activate.ps1
git switch main
git pull
pip install -r requirements.txt
git switch -c feature/<모듈>-<작업>      # 새 작업이면
# 또는 git switch feature/<기존브랜치> → git merge main   # 이어서 하는 작업이면

# ── 작업 중: 자주 반복 ──
git status
git add <파일>
git commit -m "feat(<모듈>): <내용>"
git push

# ── 작업 완료 ──
# GitHub에서 PR 생성 → 리뷰어 지정 → 팀 채널에 링크 공유
# 승인 후 Squash and merge → Delete branch

# ── merge 후 정리 ──
git switch main
git pull
git branch -D feature/<모듈>-<작업>
git fetch --prune
```

막히면 명령어를 이것저것 시도하기 전에 **`git status` 출력을 캡처해서 팀 채널에 공유**해 주세요. 대부분 그 한 화면으로 해결할 수 있습니다.
