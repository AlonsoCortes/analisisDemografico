"""
GUI - Municipios colindantes
Identifica los municipios que comparten frontera con el municipio de interés,
usando el Marco Geoestadístico Nacional (MGN) del INEGI.

Ejecutar:
    uv run python gui/vecinos_gui.py
"""

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

import contextily as ctx
import geopandas as gpd
import pandas as pd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

# ── Constantes ────────────────────────────────────────────────────────────────
TOLERANCIA_DEFAULT = 50       # metros
RUTA_SALIDA_DEFAULT = Path("datos/vecinos")
COLOR_INTERES   = "#c0392b"
COLOR_COLINDANTE = "#5b8db8"
BG = "#f4f4f4"
ACCENT = "#2c5f8a"


# ── Lógica de análisis (sin UI) ───────────────────────────────────────────────

def cargar_municipios(ruta: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(ruta)
    gdf["CVE_ENT"] = gdf["CVEGEO"].astype(str).str.zfill(5).str[:2]
    gdf["CVE_MUN"] = gdf["CVEGEO"].astype(str).str.zfill(5).str[2:]
    return gdf


def encontrar_colindantes(
    municipios: gpd.GeoDataFrame,
    nom_ent: str,
    nomgeo: str,
    tolerancia_m: int,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Devuelve (municipio_interes_gdf, colindantes_gdf)."""
    mascara = (
        (municipios["NOM_ENT"] == nom_ent) &
        (municipios["NOMGEO"]  == nomgeo)
    )
    if mascara.sum() != 1:
        raise ValueError(
            f"Se encontraron {mascara.sum()} registros para '{nomgeo}, {nom_ent}'."
        )

    idx_interes  = municipios.index[mascara][0]
    geom_interes = municipios.at[idx_interes, "geometry"]
    geom_buffer  = geom_interes.buffer(tolerancia_m)

    colindantes_mask = (
        municipios.geometry.intersects(geom_buffer)
    ) & (municipios.index != idx_interes)

    return municipios[mascara], municipios[colindantes_mask].copy()


def exportar(
    mun_gdf: gpd.GeoDataFrame,
    vec_gdf: gpd.GeoDataFrame,
    ruta_salida: Path,
    cvegeo: str,
) -> tuple[Path, Path]:
    ruta_salida.mkdir(parents=True, exist_ok=True)

    mun_export = mun_gdf.copy()
    mun_export["rol"] = "municipio_interes"

    vec_export = vec_gdf.copy()
    vec_export["rol"] = "colindante"

    combinado = gpd.GeoDataFrame(
        pd.concat([mun_export, vec_export], ignore_index=True),
        geometry="geometry", crs=mun_gdf.crs,
    )

    gpkg_path = ruta_salida / f"vecinos_{cvegeo}.gpkg"
    combinado.to_file(gpkg_path, layer="municipios", driver="GPKG")

    csv_path = ruta_salida / f"vecinos_{cvegeo}.csv"
    combinado[["CVEGEO", "CVE_ENT", "CVE_MUN", "NOM_ENT", "NOMGEO", "rol"]].reset_index(
        drop=True
    ).to_csv(csv_path, index=False, encoding="utf-8-sig")

    return gpkg_path, csv_path


# ── Aplicación ────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Municipios colindantes — Análisis Demográfico")
        self.resizable(True, True)
        self.configure(bg=BG)
        self.minsize(1000, 640)

        # Estado interno
        self.municipios: gpd.GeoDataFrame | None = None
        self.mun_interes: gpd.GeoDataFrame | None = None
        self.colindantes: gpd.GeoDataFrame | None = None
        self.ruta_mgn = tk.StringVar()
        self.ruta_salida = tk.StringVar(value=str(RUTA_SALIDA_DEFAULT))

        self._construir_ui()

    # ── Construcción de la interfaz ───────────────────────────────────────────

    def _construir_ui(self):
        # ── Barra superior: carga de archivo ──────────────────────────────────
        top = tk.Frame(self, bg=ACCENT, pady=6, padx=10)
        top.pack(fill="x")

        tk.Label(
            top, text="Marco Geoestadístico Nacional:", bg=ACCENT, fg="white",
            font=("Segoe UI", 10)
        ).pack(side="left")

        tk.Entry(
            top, textvariable=self.ruta_mgn, width=55,
            font=("Segoe UI", 9), relief="flat"
        ).pack(side="left", padx=(6, 4))

        tk.Button(
            top, text="Examinar…", command=self._seleccionar_mgn,
            bg="white", fg=ACCENT, relief="flat", padx=8,
            font=("Segoe UI", 9, "bold"), cursor="hand2"
        ).pack(side="left")

        tk.Button(
            top, text="Cargar", command=self._cargar_mgn,
            bg="#e8f0fe", fg=ACCENT, relief="flat", padx=10,
            font=("Segoe UI", 9, "bold"), cursor="hand2"
        ).pack(side="left", padx=4)

        # ── Cuerpo principal ──────────────────────────────────────────────────
        cuerpo = tk.Frame(self, bg=BG)
        cuerpo.pack(fill="both", expand=True)

        panel_izq = self._panel_izquierdo(cuerpo)
        panel_izq.pack(side="left", fill="y", padx=10, pady=10)

        panel_der = self._panel_derecho(cuerpo)
        panel_der.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=10)

        # ── Barra de estado ───────────────────────────────────────────────────
        self.status = tk.StringVar(value="Carga el shapefile del MGN para comenzar.")
        tk.Label(
            self, textvariable=self.status, bg="#dce3ea", anchor="w",
            font=("Segoe UI", 9), padx=10, pady=4
        ).pack(fill="x", side="bottom")

    def _panel_izquierdo(self, parent) -> tk.Frame:
        f = tk.Frame(parent, bg=BG, width=260)
        f.pack_propagate(False)

        def seccion(texto):
            tk.Label(
                f, text=texto, bg=BG, fg="#555", anchor="w",
                font=("Segoe UI", 8, "bold")
            ).pack(fill="x", pady=(12, 2))

        # ── Selección de municipio ─────────────────────────────────────────
        seccion("MUNICIPIO DE INTERÉS")

        tk.Label(f, text="Estado:", bg=BG, anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x")
        self.cb_estado = ttk.Combobox(f, state="disabled", font=("Segoe UI", 9))
        self.cb_estado.pack(fill="x", pady=(0, 6))
        self.cb_estado.bind("<<ComboboxSelected>>", self._on_estado_cambio)

        tk.Label(f, text="Municipio:", bg=BG, anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x")
        self.cb_municipio = ttk.Combobox(f, state="disabled", font=("Segoe UI", 9))
        self.cb_municipio.pack(fill="x", pady=(0, 6))

        # ── Tolerancia ────────────────────────────────────────────────────
        seccion("PARÁMETROS")

        tk.Label(f, text="Tolerancia de frontera (metros):", bg=BG, anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x")

        tol_frame = tk.Frame(f, bg=BG)
        tol_frame.pack(fill="x", pady=(0, 6))
        self.tolerancia = tk.IntVar(value=TOLERANCIA_DEFAULT)
        tk.Spinbox(
            tol_frame, from_=1, to=500, increment=10,
            textvariable=self.tolerancia, width=6,
            font=("Segoe UI", 9)
        ).pack(side="left")
        tk.Label(tol_frame, text="m", bg=BG, font=("Segoe UI", 9)).pack(side="left", padx=4)

        # ── Salida ────────────────────────────────────────────────────────
        seccion("EXPORTACIÓN")

        tk.Label(f, text="Carpeta de salida:", bg=BG, anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x")

        sal_frame = tk.Frame(f, bg=BG)
        sal_frame.pack(fill="x", pady=(0, 10))
        tk.Entry(sal_frame, textvariable=self.ruta_salida,
                 font=("Segoe UI", 8), width=20).pack(side="left", fill="x", expand=True)
        tk.Button(
            sal_frame, text="…", command=self._seleccionar_salida,
            font=("Segoe UI", 9), padx=4, relief="flat", bg="#ddd", cursor="hand2"
        ).pack(side="left", padx=(4, 0))

        # ── Botones de acción ─────────────────────────────────────────────
        self.btn_analizar = tk.Button(
            f, text="Analizar colindantes",
            command=self._analizar,
            state="disabled",
            bg=ACCENT, fg="white", relief="flat",
            font=("Segoe UI", 10, "bold"), pady=8, cursor="hand2"
        )
        self.btn_analizar.pack(fill="x", pady=(6, 4))

        self.btn_exportar = tk.Button(
            f, text="Exportar resultados",
            command=self._exportar,
            state="disabled",
            bg="#27ae60", fg="white", relief="flat",
            font=("Segoe UI", 10, "bold"), pady=8, cursor="hand2"
        )
        self.btn_exportar.pack(fill="x")

        return f

    def _panel_derecho(self, parent) -> tk.Frame:
        f = tk.Frame(parent, bg=BG)

        # ── Mapa ──────────────────────────────────────────────────────────
        self.fig, self.ax = plt.subplots(figsize=(7, 5), facecolor=BG)
        self.ax.set_facecolor(BG)
        self.ax.axis("off")
        self.ax.text(
            0.5, 0.5, "El mapa aparecerá aquí\ndespués de analizar.",
            ha="center", va="center", transform=self.ax.transAxes,
            fontsize=11, color="#aaa"
        )
        self.fig.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.fig, master=f)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        toolbar_frame = tk.Frame(f, bg=BG)
        toolbar_frame.pack(fill="x")
        NavigationToolbar2Tk(self.canvas, toolbar_frame)

        # ── Tabla de resultados ───────────────────────────────────────────
        tabla_frame = tk.Frame(f, bg=BG)
        tabla_frame.pack(fill="x", pady=(6, 0))

        cols = ("CVEGEO", "Estado", "Municipio")
        self.tabla = ttk.Treeview(
            tabla_frame, columns=cols, show="headings", height=5
        )
        for col in cols:
            self.tabla.heading(col, text=col)
        self.tabla.column("CVEGEO",    width=80,  anchor="center")
        self.tabla.column("Estado",    width=160, anchor="w")
        self.tabla.column("Municipio", width=200, anchor="w")

        scroll = ttk.Scrollbar(tabla_frame, orient="vertical",
                               command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll.set)
        self.tabla.pack(side="left", fill="x", expand=True)
        scroll.pack(side="left", fill="y")

        return f

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _seleccionar_mgn(self):
        ruta = filedialog.askopenfilename(
            title="Seleccionar shapefile de municipios",
            filetypes=[("Shapefile", "*.shp"), ("Todos los archivos", "*.*")]
        )
        if ruta:
            self.ruta_mgn.set(ruta)

    def _seleccionar_salida(self):
        ruta = filedialog.askdirectory(title="Seleccionar carpeta de salida")
        if ruta:
            self.ruta_salida.set(ruta)

    def _cargar_mgn(self):
        ruta = self.ruta_mgn.get().strip()
        if not ruta:
            messagebox.showwarning("Aviso", "Selecciona primero el shapefile del MGN.")
            return
        if not Path(ruta).exists():
            messagebox.showerror("Error", f"No se encontró el archivo:\n{ruta}")
            return

        self._set_status("Cargando shapefile… esto puede tardar unos segundos.")
        self.btn_analizar.config(state="disabled")
        threading.Thread(target=self._tarea_cargar, args=(ruta,), daemon=True).start()

    def _tarea_cargar(self, ruta: str):
        try:
            self.municipios = cargar_municipios(Path(ruta))
            estados = sorted(self.municipios["NOM_ENT"].dropna().unique().tolist())
            self.after(0, self._poblar_estados, estados)
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error al cargar", str(e)))
            self.after(0, self._set_status, "Error al cargar el shapefile.")

    def _poblar_estados(self, estados: list[str]):
        self.cb_estado.config(values=estados, state="readonly")
        self.cb_estado.set(estados[0])
        self._on_estado_cambio(None)
        self.btn_analizar.config(state="normal")
        self._set_status(
            f"Shapefile cargado: {len(self.municipios)} municipios. "
            "Selecciona estado y municipio."
        )

    def _on_estado_cambio(self, _event):
        if self.municipios is None:
            return
        estado = self.cb_estado.get()
        muns = sorted(
            self.municipios.loc[
                self.municipios["NOM_ENT"] == estado, "NOMGEO"
            ].dropna().unique().tolist()
        )
        self.cb_municipio.config(values=muns, state="readonly")
        self.cb_municipio.set(muns[0] if muns else "")

    def _analizar(self):
        if self.municipios is None:
            return
        nom_ent = self.cb_estado.get()
        nomgeo  = self.cb_municipio.get()
        if not nom_ent or not nomgeo:
            messagebox.showwarning("Aviso", "Selecciona un estado y un municipio.")
            return

        self.btn_analizar.config(state="disabled")
        self.btn_exportar.config(state="disabled")
        self._set_status("Calculando colindantes…")

        threading.Thread(
            target=self._tarea_analizar,
            args=(nom_ent, nomgeo, self.tolerancia.get()),
            daemon=True
        ).start()

    def _tarea_analizar(self, nom_ent: str, nomgeo: str, tolerancia: int):
        try:
            mun, vec = encontrar_colindantes(
                self.municipios, nom_ent, nomgeo, tolerancia
            )
            self.mun_interes = mun
            self.colindantes = vec
            self.after(0, self._mostrar_resultados, mun, vec)
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error en análisis", str(e)))
            self.after(0, self.btn_analizar.config, {"state": "normal"})
            self.after(0, self._set_status, "Error durante el análisis.")

    def _mostrar_resultados(
        self,
        mun: gpd.GeoDataFrame,
        vec: gpd.GeoDataFrame,
    ):
        # ── Tabla ──────────────────────────────────────────────────────────
        for row in self.tabla.get_children():
            self.tabla.delete(row)
        for _, r in vec.iterrows():
            self.tabla.insert(
                "", "end",
                values=(r["CVEGEO"], r["NOM_ENT"], r["NOMGEO"])
            )

        # ── Mapa ───────────────────────────────────────────────────────────
        self.ax.cla()
        self.ax.axis("off")

        CRS_VIZ = "EPSG:3857"
        mun_plot = mun.to_crs(CRS_VIZ)
        vec_plot = vec.to_crs(CRS_VIZ)

        vec_plot.plot(
            ax=self.ax, color=COLOR_COLINDANTE,
            alpha=0.6, edgecolor="white", linewidth=0.5
        )
        mun_plot.plot(
            ax=self.ax, color=COLOR_INTERES,
            edgecolor="black", linewidth=1.0, zorder=5
        )

        for _, row in vec_plot.iterrows():
            cx = row.geometry.centroid.x
            cy = row.geometry.centroid.y
            self.ax.annotate(
                row["NOMGEO"], xy=(cx, cy),
                fontsize=6, ha="center", va="center", color="#1a1a1a",
                bbox=dict(
                    boxstyle="round,pad=0.15", fc="white", alpha=0.6, ec="none"
                )
            )

        try:
            ctx.add_basemap(
                self.ax, source=ctx.providers.CartoDB.Positron, zoom="auto"
            )
        except Exception:
            pass

        mun_row = mun.iloc[0]
        leyenda = [
            mpatches.Patch(
                color=COLOR_INTERES,
                label=f"Municipio de interés: {mun_row['NOMGEO']}"
            ),
            mpatches.Patch(
                color=COLOR_COLINDANTE,
                label=f"Municipios colindantes ({len(vec)})"
            ),
        ]
        self.ax.legend(
            handles=leyenda, loc="lower left",
            fontsize=8, framealpha=0.9
        )
        self.ax.set_title(
            f"{mun_row['NOMGEO']}, {mun_row['NOM_ENT']}  "
            f"(CVEGEO: {mun_row['CVEGEO']})",
            fontsize=10, fontweight="bold", pad=8
        )

        self.fig.tight_layout()
        self.canvas.draw()

        # ── Estado ─────────────────────────────────────────────────────────
        self.btn_analizar.config(state="normal")
        self.btn_exportar.config(state="normal")
        self._set_status(
            f"✔  {len(vec)} municipios colindantes de {mun_row['NOMGEO']}. "
            "Puedes exportar los resultados."
        )

    def _exportar(self):
        if self.mun_interes is None or self.colindantes is None:
            return
        ruta = Path(self.ruta_salida.get())
        cvegeo = self.mun_interes.iloc[0]["CVEGEO"]
        try:
            gpkg, csv = exportar(self.mun_interes, self.colindantes, ruta, cvegeo)
            messagebox.showinfo(
                "Exportación completada",
                f"Archivos guardados en:\n{ruta}\n\n"
                f"• {gpkg.name}\n• {csv.name}"
            )
            self._set_status(f"Exportado en {ruta}")
        except Exception as e:
            messagebox.showerror("Error al exportar", str(e))

    def _set_status(self, msg: str):
        self.status.set(msg)

    # ── Limpieza al cerrar ────────────────────────────────────────────────────

    def destroy(self):
        plt.close(self.fig)
        super().destroy()


# ── Punto de entrada ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
