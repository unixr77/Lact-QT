"""Fan eğrisi veri modeli (Qt'a bağımlı değil)."""

DEFAULT_CURVE = {"40": 0.30, "50": 0.35, "60": 0.50, "70": 0.75, "80": 1.00}


class CurveModel:
    """Sıcaklık (°C, tam sayı) -> fan hızı (0..1) noktaları; sıcaklığa göre sıralı."""

    def __init__(self, t_min=25, t_max=100, s_min=0.30, s_max=1.0):
        # fixed_count: RDNA3+ kartlarda LACT eğrinin tam 5 noktalı olmasını ister.
        self.fixed_count = None
        self.set_limits(t_min, t_max, s_min, s_max)
        self.points = []
        self.load(DEFAULT_CURVE)

    def set_limits(self, t_min, t_max, s_min, s_max):
        self.t_min, self.t_max = int(t_min), int(t_max)
        self.s_min, self.s_max = float(s_min), float(s_max)
        if getattr(self, "points", None):
            self.points = [[self._clamp_t(t), self._clamp_s(s)] for t, s in self.points]

    def _clamp_t(self, t):
        return min(max(int(round(t)), self.t_min), self.t_max)

    def _clamp_s(self, s):
        return min(max(round(float(s), 2), self.s_min), self.s_max)

    def load(self, mapping):
        """{"40": 0.3, ...} sözlüğünden yükler; geçersizse varsayılan eğriyi kullanır."""
        pts = {}
        for key, value in (mapping or {}).items():
            try:
                pts[self._clamp_t(float(key))] = self._clamp_s(value)
            except (TypeError, ValueError):
                continue
        if len(pts) < 2:
            pts = {self._clamp_t(float(k)): self._clamp_s(v) for k, v in DEFAULT_CURVE.items()}
        self.points = [[t, s] for t, s in sorted(pts.items())]
        if self.fixed_count and len(self.points) != self.fixed_count:
            self._resample(self.fixed_count)

    def _resample(self, n):
        """Eğriyi, aynı sıcaklık aralığında n eşit aralıklı noktaya yeniden örnekler."""
        t0, t1 = self.points[0][0], self.points[-1][0]
        if t1 - t0 < n - 1:  # tamsayı sıcaklıklarla n ayrı nokta sığmıyor
            t0, t1 = self.t_min, self.t_max
        temps = [int(round(t0 + (t1 - t0) * i / (n - 1))) for i in range(n)]
        speeds = [self.speed_at(t) for t in temps]
        self.points = [[t, self._clamp_s(s)] for t, s in zip(temps, speeds)]

    def to_config(self):
        return {str(t): s for t, s in self.points}

    def move(self, index, temp, speed):
        """Noktayı taşır; komşu noktaların sıcaklık sırasını bozmaz."""
        lo = self.points[index - 1][0] + 1 if index > 0 else self.t_min
        hi = self.points[index + 1][0] - 1 if index < len(self.points) - 1 else self.t_max
        self.points[index] = [min(max(int(round(temp)), lo), max(hi, lo)), self._clamp_s(speed)]

    def add(self, temp, speed):
        """Yeni nokta ekler; o sıcaklıkta nokta varsa ya da sayı sabitse None döndürür."""
        if self.fixed_count:
            return None
        temp = self._clamp_t(temp)
        if any(t == temp for t, _ in self.points):
            return None
        self.points.append([temp, self._clamp_s(speed)])
        self.points.sort(key=lambda p: p[0])
        return next(i for i, p in enumerate(self.points) if p[0] == temp)

    def remove(self, index):
        """En az iki nokta kalmalıdır; nokta sayısı sabitse silinemez."""
        if self.fixed_count or len(self.points) <= 2:
            return False
        del self.points[index]
        return True

    def speed_at(self, temp):
        """Verilen sıcaklıkta eğrinin doğrusal ara değerle hesaplanan hızı."""
        pts = self.points
        if temp <= pts[0][0]:
            return pts[0][1]
        if temp >= pts[-1][0]:
            return pts[-1][1]
        for (t0, s0), (t1, s1) in zip(pts, pts[1:]):
            if t0 <= temp <= t1:
                return s0 + (s1 - s0) * (temp - t0) / (t1 - t0)
        return pts[-1][1]
