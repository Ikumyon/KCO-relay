import tkinter as tk

class ROISelector:
    """範囲選択のための透明ウィンドウ"""
    def __init__(self, root, callback):
        self.root = root
        self.callback = callback
        self.start_x = None
        self.start_y = None
        self.rect = None
        
        # 選択用トップレベルウィンドウ
        self.top = tk.Toplevel(root)
        self.top.attributes('-fullscreen', True)
        self.top.attributes('-alpha', 0.3)  # 半透明
        self.top.configure(background='black')
        self.top.resizable(False, False)
        
        # イベントバインド
        self.canvas = tk.Canvas(self.top, cursor="cross", bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Escape>", self.cancel)
        
        # 説明ラベル
        self.label = tk.Label(self.top, text="ドラッグしてOCR対象範囲を選択してください (Escでキャンセル)", 
                              fg="white", bg="black", font=("Arial", 14))
        self.label.place(relx=0.5, rely=0.1, anchor="center")

    def on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(self.start_x, self.start_y, self.start_x, self.start_y, outline='red', width=2)

    def on_drag(self, event):
        cur_x, cur_y = (event.x, event.y)
        self.canvas.coords(self.rect, self.start_x, self.start_y, cur_x, cur_y)

    def on_release(self, event):
        end_x, end_y = (event.x, event.y)
        
        # 座標の正規化 (左上, 右下)
        x1 = min(self.start_x, end_x)
        y1 = min(self.start_y, end_y)
        x2 = max(self.start_x, end_x)
        y2 = max(self.start_y, end_y)
        
        # 幅・高さが小さすぎる場合は無視
        if (x2 - x1) < 10 or (y2 - y1) < 10:
            self.cancel()
            return

        self.top.destroy()
        if self.callback:
            self.callback((x1, y1, x2, y2))

    def cancel(self, event=None):
        self.top.destroy()
