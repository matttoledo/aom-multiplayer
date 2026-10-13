import time

from Xlib import X
from Xlib.display import Display

MARK_X, MARK_Y, MARK_W, MARK_H = 1200, 680, 64, 32
MAGENTA = 0xFF00FF


class Marker:
    """Quadrado magenta no display, para medir a latencia captura->tela sem suposicao:
    o servidor desenha e informa o instante; o navegador procura o pixel no frame que
    acabou de ir para a tela. Funciona igual nos dois caminhos (MSE e WebRTC).

    O atributo `background` nao basta: o servidor X nao limpa janela override-redirect
    ao mapear, entao o preenchimento e feito com GC.
    """

    def __init__(self, disp):
        self.disp = disp
        self.root = disp.screen().root
        self.win = None
        self.gc = None

    def draw(self):
        if self.win is None:
            self.win = self.root.create_window(
                x=MARK_X, y=MARK_Y, width=MARK_W, height=MARK_H,
                border_width=0, depth=X.CopyFromParent, override_redirect=True)
            self.win.map()
            self.win.raise_window()
            self.gc = self.win.create_gc(foreground=MAGENTA, background=MAGENTA)
        self.win.poly_fill_rectangle(self.gc, [(0, 0, MARK_W, MARK_H)])
        self.disp.sync()
        return time.time()

    def hide(self):
        if self.win:
            self.win.unmap()
            self.disp.sync()

    def close(self):
        if self.win:
            self.win.destroy()
            self.disp.sync()
