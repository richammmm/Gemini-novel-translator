import os
import json
import time
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai  # pip install google-genai

MODEL_NAME = "gemini-flash-latest"

# 범용 프롬프트
DEFAULT_PROMPT = """당신은 유능한 소설 번역가입니다.
아래 제공된 원문을 한국어로 번역하세요.
- 서술: '~다' 문체
- 대사: 인물 관계와 상황에 맞춰 자연스럽게
- 원문 특유의 표현, 의성어·의태어, 농담: 가능한 한 원문 뉘앙스 유지
- 고유명사: 첫 등장 시 원어 병기, 이후 음역
- 대사는 큰따옴표(“ ”) 사용
번역 시작:
"""


def split_text(text, max_length):
    paras = text.split("\n\n")
    chunks, current = [], ""
    for para in paras:
        if len(current) + len(para) < max_length:
            current += ("\n\n" + para) if current else para
        else:
            chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return chunks


def load_cache(cache_path):
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache, cache_path):
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def translate_chunk(client, chunk, prompt, idx, max_retry, retry_wait, model_name):
    full_prompt = prompt.strip() + "\n\n" + chunk
    for retry in range(1, max_retry + 1):
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=full_prompt,
            )

            out_text = None
            if hasattr(resp, "text") and resp.text:
                out_text = resp.text
            elif hasattr(resp, "candidates") and resp.candidates:
                try:
                    out_text = resp.candidates[0].content.parts[0].text
                except Exception:
                    out_text = None

            if out_text:
                return out_text.strip()

        except Exception as e:
            print(f"[ERR] 청크 {idx} 번역 실패({retry}/{max_retry}): {e}")

        time.sleep(retry_wait)
    return None


class TranslatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Novel Translator")
        self.api_key = None
        self.file_path = None
        self.translated_path = None
        self.cache_path = None
        self.chunks = []
        self.status_var = tk.StringVar(value="대기 중")
        self.setup_ui()

    def setup_ui(self):
        f = tk.Frame(self.root)
        f.pack(padx=20, pady=20)

        tk.Button(f, text="API 키 입력", command=self.ask_api_key).pack(fill='x', pady=3)
        tk.Button(f, text="원문 파일 선택", command=self.pick_file).pack(fill='x', pady=3)

        # 파라미터 입력란
        p = tk.Frame(f)
        p.pack(fill='x', pady=(7, 0))

        tk.Label(p, text="병렬 스레드 개수(1~20)").grid(row=0, column=0)
        self.workers_val = tk.IntVar(value=5)
        tk.Scale(p, from_=1, to=20, orient=tk.HORIZONTAL, variable=self.workers_val, length=150).grid(row=0, column=1)

        tk.Label(p, text="재시도 횟수(1~10)").grid(row=1, column=0)
        self.retry_val = tk.IntVar(value=3)
        tk.Scale(p, from_=1, to=10, orient=tk.HORIZONTAL, variable=self.retry_val, length=150).grid(row=1, column=1)

        tk.Label(p, text="재시도 간격(초)").grid(row=2, column=0)
        self.retrywait_val = tk.IntVar(value=5)
        tk.Scale(p, from_=1, to=30, orient=tk.HORIZONTAL, variable=self.retrywait_val, length=150).grid(row=2, column=1)

        tk.Label(p, text="분할 글자수(5,000~50,000)").grid(row=3, column=0)
        self.chunk_val = tk.IntVar(value=20000)
        tk.Scale(p, from_=5000, to=50000, orient=tk.HORIZONTAL, variable=self.chunk_val, length=150, resolution=1000).grid(row=3, column=1)

        tk.Label(f, text="프롬프트(수정 가능)").pack(anchor='w', pady=(13, 0))
        self.prompt_box = tk.Text(f, height=10, width=80)
        self.prompt_box.pack()
        self.prompt_box.insert(tk.END, DEFAULT_PROMPT)

        tk.Button(f, text="번역 시작", command=self.start_translate_thread).pack(fill='x', pady=8)
        tk.Label(f, textvariable=self.status_var, fg='blue').pack()

    def ask_api_key(self):
        key = simpledialog.askstring("API 키 입력", "Google AI Studio API 키를 입력하세요:", show='*')
        if key:
            self.api_key = key.strip()
            messagebox.showinfo("완료", "API 키가 등록되었습니다.")

    def pick_file(self):
        path = filedialog.askopenfilename(filetypes=[("텍스트 파일", "*.txt")])
        if path:
            self.file_path = path
            self.translated_path = os.path.splitext(path)[0] + "_번역결과.txt"
            self.cache_path = os.path.splitext(path)[0] + "_cache.json"
            self.status_var.set(f"선택됨: {self.file_path}")

    def start_translate_thread(self):
        if not self.api_key:
            messagebox.showerror("오류", "API 키를 먼저 입력하세요.")
            return
        if not self.file_path:
            messagebox.showerror("오류", "원문 파일을 선택하세요.")
            return
        t = threading.Thread(target=self.translate_main)
        t.start()

    def translate_main(self):
        try:
            prompt = self.prompt_box.get("1.0", tk.END)
            max_workers = self.workers_val.get()
            max_retry = self.retry_val.get()
            retry_wait = self.retrywait_val.get()
            chunk_size = self.chunk_val.get()

            with open(self.file_path, "r", encoding="utf-8") as f:
                novel_text = f.read()

            self.chunks = split_text(novel_text, max_length=chunk_size)
            total = len(self.chunks)
            self.status_var.set(f"총 {total}개 청크. 번역 시작...")
            self.root.update()

            cache = load_cache(self.cache_path)
            failed = []

            client = genai.Client(api_key=self.api_key)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for i, chunk in enumerate(self.chunks):
                    # 이미 캐시에 있는 청크는 다시 요청하지 않음
                    if str(i) in cache:
                        continue
                    futures[executor.submit(
                        translate_chunk, client, chunk, prompt, i,
                        max_retry, retry_wait, MODEL_NAME
                    )] = i

                done_cnt = len(cache)
                for future in as_completed(futures):
                    idx = futures[future]
                    result = future.result()
                    if result:
                        # 청크 하나가 성공할 때마다 즉시 캐시에 저장
                        cache[str(idx)] = result
                        save_cache(cache, self.cache_path)
                        done_cnt += 1
                        self.status_var.set(f"진행: {done_cnt}/{total} 완료")
                        self.root.update()
                    else:
                        failed.append(idx)
                        self.status_var.set(f"진행: {done_cnt}/{total}, 실패 {len(failed)}개")
                        self.root.update()

            if all(str(i) in cache for i in range(len(self.chunks))):
                final_text = "\n\n".join(cache[str(i)] for i in range(len(self.chunks)))
                final_text += "\n\n번역이 완료됐습니다."
                with open(self.translated_path, "w", encoding="utf-8") as f:
                    f.write(final_text)
                self.status_var.set(f"완료! {self.translated_path}")
                messagebox.showinfo("완료", f"번역 끝! 결과 파일: {self.translated_path}")
            else:
                self.status_var.set("일부 실패. 재실행시 실패 청크만 재도전.")
                messagebox.showwarning("경고", "일부 청크 번역 실패. 재실행하면 실패한 부분만 처리합니다.")

        except Exception as e:
            self.status_var.set(f"작업 중 에러: {e}")
            messagebox.showerror("오류", str(e))


if __name__ == "__main__":
    root = tk.Tk()
    app = TranslatorApp(root)
    root.mainloop()