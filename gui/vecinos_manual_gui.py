"""
GUI - Municipios vecinos (selección manual + mapa interactivo)
Muestra el área circundante al municipio de interés; haz clic en el mapa
para agregar o quitar vecinos. El dropdown sirve como alternativa para
municipios fuera del área visible.

Ejecutar:
    uv run python gui/vecinos_manual_gui.py
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
from shapely.geometry import Point, box as shp_box

# ── Constantes ────────────────────────────────────────────────────────────────
RUTA_SALIDA_DEFAULT = Path("datos/vecinos")
COLOR_INTERES    = "#c0392b"
COLOR_VECINO     = "#5b8db8"
COLOR_CONTEXTO   = "#e8e8e8"
COLOR_CTX_EDGE   = "#aaaaaa"
BG               = "#f4f4f4"
ACCENT           = "#2c5f8a"
FACTOR_CONTEXTO  = 2.0   # extensión del área de contexto (factor del tamaño del municipio)
MIN_EXTENT_M     = 10_000  # extensión mínima del municipio (10 km)


# ── Lógica de análisis (sin UI) ───────────────────────────────────────────────

def cargar_municipios(ruta: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(ruta)
    gdf["CVE_ENT"] = gdf["CVEGEO"].astype(str).str.zfill(5).str[:2]
    gdf["CVE_MUN"] = gdf["CVEGEO"].astype(str).str.zfill(5).str[2:]
    return gdf


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
    vec_export["rol"] = "vecino"

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


def calcular_contexto(
    municipios: gpd.GeoDataFrame,
    mun_interes: gpd.GeoDataFrame,
    factor: float,
) -> gpd.GeoDataFrame:
    """Municipios dentro del área extendida alrededor del municipio de interés."""
    geom = mun_interes.geometry.iloc[0]
    minx, miny, maxx, maxy = geom.bounds
    dx = max(maxx - minx, MIN_EXTENT_M)
    dy = max(maxy - miny, MIN_EXTENT_M)
    area_ext = shp_box(
        minx - factor * dx, miny - factor * dy,
        maxx + factor * dx, maxy + factor * dy,
    )
    mask = municipios.geometry.intersects(area_ext)
    return municipios[mask].to_crs("EPSG:3857")


# ── Aplicación ────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Municipios vecinos — Selección manual")
        self.resizable(True, True)
        self.configure(bg=BG)
        self.minsize(1060, 680)

        self.municipios: gpd.GeoDataFrame | None = None
        self.mun_interes: gpd.GeoDataFrame | None = None
        self.contexto_3857: gpd.GeoDataFrame | None = None
        self.vecinos_items: list[dict] = []
        self._click_cid = None

        self.ruta_mgn    = tk.StringVar()
        self.ruta_salida = tk.StringVar(value=str(RUTA_SALIDA_DEFAULT))

        self._construir_ui()

    # ── Construcción de la interfaz ───────────────────────────────────────────

    def _construir_ui(self):
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

        cuerpo = tk.Frame(self, bg=BG)
        cuerpo.pack(fill="both", expand=True)
        self._panel_izquierdo(cuerpo).pack(side="left", fill="y", padx=10, pady=10)
        self._panel_derecho(cuerpo).pack(
            side="left", fill="both", expand=True, padx=(0, 10), pady=10
        )

        self.status = tk.StringVar(value="Carga el shapefile del MGN para comenzar.")
        tk.Label(
            self, textvariable=self.status, bg="#dce3ea", anchor="w",
            font=("Segoe UI", 9), padx=10, pady=4
        ).pack(fill="x", side="bottom")

    def _panel_izquierdo(self, parent) -> tk.Frame:
        f = tk.Frame(parent, bg=BG, width=280)
        f.pack_propagate(False)

        def sec(texto):
            tk.Label(
                f, text=texto, bg=BG, fg="#555", anchor="w",
                font=("Segoe UI", 8, "bold")
            ).pack(fill="x", pady=(12, 2))

        # ── Municipio de interés ──────────────────────────────────────────
        sec("MUNICIPIO DE INTERÉS")

        tk.Label(f, text="Estado:", bg=BG, anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x")
        self.cb_estado = ttk.Combobox(f, state="disabled", font=("Segoe UI", 9))
        self.cb_estado.pack(fill="x", pady=(0, 4))
        self.cb_estado.bind("<<ComboboxSelected>>", self._on_estado_cambio)

        tk.Label(f, text="Municipio:", bg=BG, anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x")
        self.cb_municipio = ttk.Combobox(f, state="disabled", font=("Segoe UI", 9))
        self.cb_municipio.pack(fill="x", pady=(0, 6))

        self.btn_confirmar = tk.Button(
            f, text="Confirmar selección",
            command=self._confirmar_municipio,
            state="disabled",
            bg=ACCENT, fg="white", relief="flat",
            font=("Segoe UI", 9, "bold"), pady=6, cursor="hand2"
        )
        self.btn_confirmar.pack(fill="x", pady=(0, 4))

        # ── Agregar vecino vía dropdown ───────────────────────────────────
        sec("AGREGAR VECINO (dropdown)")
        tk.Label(
            f, text="Alternativa al clic en el mapa.", bg=BG, anchor="w",
            font=("Segoe UI", 8), fg="#888"
        ).pack(fill="x", pady=(0, 4))

        tk.Label(f, text="Estado:", bg=BG, anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x")
        self.cb_estado_vec = ttk.Combobox(f, state="disabled", font=("Segoe UI", 9))
        self.cb_estado_vec.pack(fill="x", pady=(0, 4))
        self.cb_estado_vec.bind("<<ComboboxSelected>>", self._on_estado_vec_cambio)

        tk.Label(f, text="Municipio:", bg=BG, anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x")
        self.cb_municipio_vec = ttk.Combobox(f, state="disabled", font=("Segoe UI", 9))
        self.cb_municipio_vec.pack(fill="x", pady=(0, 6))

        self.btn_agregar = tk.Button(
            f, text="+ Agregar a la lista",
            command=self._agregar_vecino,
            state="disabled",
            bg="#27ae60", fg="white", relief="flat",
            font=("Segoe UI", 9, "bold"), pady=6, cursor="hand2"
        )
        self.btn_agregar.pack(fill="x")

        # ── Exportación ───────────────────────────────────────────────────
        sec("EXPORTACIÓN")

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

        self.btn_exportar = tk.Button(
            f, text="Exportar resultados",
            command=self._exportar,
            state="disabled",
            bg="#8e44ad", fg="white", relief="flat",
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
            0.5, 0.5,
            "El mapa aparecerá aquí.\n"
            "Confirma el municipio de interés para comenzar.",
            ha="center", va="center", transform=self.ax.transAxes,
            fontsize=11, color="#aaa"
        )
        self.fig.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.fig, master=f)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        toolbar_frame = tk.Frame(f, bg=BG)
        toolbar_frame.pack(fill="x")
        NavigationToolbar2Tk(self.canvas, toolbar_frame)

        # ── Cabecera de tabla ─────────────────────────────────────────────
        tabla_header = tk.Frame(f, bg=BG)
        tabla_header.pack(fill="x", pady=(6, 0))

        tk.Label(
            tabla_header, text="Vecinos seleccionados:", bg=BG,
            font=("Segoe UI", 9, "bold"), fg="#333"
        ).pack(side="left")

        self.btn_quitar = tk.Button(
            tabla_header, text="Quitar seleccionado",
            command=self._quitar_vecino,
            state="disabled",
            bg="#e74c3c", fg="white", relief="flat",
            font=("Segoe UI", 8, "bold"), padx=8, pady=2, cursor="hand2"
        )
        self.btn_quitar.pack(side="right")

        # ── Tabla ─────────────────────────────────────────────────────────
        tabla_frame = tk.Frame(f, bg=BG)
        tabla_frame.pack(fill="x")

        cols = ("CVEGEO", "Estado", "Municipio")
        self.tabla = ttk.Treeview(
            tabla_frame, columns=cols, show="headings", height=5
        )
        for col in cols:
            self.tabla.heading(col, text=col)
        self.tabla.column("CVEGEO",    width=80,  anchor="center")
        self.tabla.column("Estado",    width=160, anchor="w")
        self.tabla.column("Municipio", width=200, anchor="w")
        self.tabla.bind("<<TreeviewSelect>>", self._on_tabla_seleccion)

        scroll = ttk.Scrollbar(tabla_frame, orient="vertical",
                               command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll.set)
        self.tabla.pack(side="left", fill="x", expand=True)
        scroll.pack(side="left", fill="y")

        return f

    # ── Callbacks de archivo ──────────────────────────────────────────────────

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

    # ── Carga del shapefile ───────────────────────────────────────────────────

    def _cargar_mgn(self):
        ruta = self.ruta_mgn.get().strip()
        if not ruta:
            messagebox.showwarning("Aviso", "Selecciona primero el shapefile del MGN.")
            return
        if not Path(ruta).exists():
            messagebox.showerror("Error", f"No se encontró el archivo:\n{ruta}")
            return
        self._set_status("Cargando shapefile… esto puede tardar unos segundos.")
        self.btn_confirmar.config(state="disabled")
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
        for cb in (self.cb_estado, self.cb_estado_vec):
            cb.config(values=estados, state="readonly")
            cb.set(estados[0])
        self._on_estado_cambio(None)
        self._on_estado_vec_cambio(None)
        self.btn_confirmar.config(state="normal")
        self._set_status(
            f"Shapefile cargado: {len(self.municipios)} municipios. "
            "Confirma el municipio de interés para comenzar."
        )

    # ── Comboboxes encadenados ────────────────────────────────────────────────

    def _on_estado_cambio(self, _event):
        if self.municipios is None:
            return
        muns = sorted(
            self.municipios.loc[
                self.municipios["NOM_ENT"] == self.cb_estado.get(), "NOMGEO"
            ].dropna().unique().tolist()
        )
        self.cb_municipio.config(values=muns, state="readonly")
        self.cb_municipio.set(muns[0] if muns else "")

    def _on_estado_vec_cambio(self, _event):
        if self.municipios is None:
            return
        muns = sorted(
            self.municipios.loc[
                self.municipios["NOM_ENT"] == self.cb_estado_vec.get(), "NOMGEO"
            ].dropna().unique().tolist()
        )
        self.cb_municipio_vec.config(values=muns, state="readonly")
        self.cb_municipio_vec.set(muns[0] if muns else "")

    # ── Confirmar municipio de interés ────────────────────────────────────────

    def _confirmar_municipio(self):
        if self.municipios is None:
            return
        nom_ent = self.cb_estado.get()
        nomgeo  = self.cb_municipio.get()
        if not nom_ent or not nomgeo:
            messagebox.showwarning("Aviso", "Selecciona un estado y un municipio.")
            return

        mascara = (
            (self.municipios["NOM_ENT"] == nom_ent) &
            (self.municipios["NOMGEO"]  == nomgeo)
        )
        if mascara.sum() != 1:
            messagebox.showerror(
                "Error",
                f"Se encontraron {mascara.sum()} registros para '{nomgeo}, {nom_ent}'."
            )
            return

        self.mun_interes = self.municipios[mascara].copy()
        self.vecinos_items.clear()
        self._vaciar_tabla()
        self.btn_exportar.config(state="disabled")
        self.btn_quitar.config(state="disabled")
        self.btn_agregar.config(state="normal")

        # Calcular área de contexto (en EPSG:3857 para mapa y consultas de clic)
        self.contexto_3857 = calcular_contexto(
            self.municipios, self.mun_interes, FACTOR_CONTEXTO
        )

        # Conectar evento de clic en el mapa
        if self._click_cid is not None:
            self.canvas.mpl_disconnect(self._click_cid)
        self._click_cid = self.canvas.mpl_connect(
            "button_press_event", self._on_map_click
        )

        self._actualizar_mapa()

        mun_row = self.mun_interes.iloc[0]
        self._set_status(
            f"Municipio de interés: {mun_row['NOMGEO']}, {mun_row['NOM_ENT']} "
            f"(CVEGEO: {mun_row['CVEGEO']}).  "
            "Haz clic en el mapa para agregar o quitar vecinos."
        )

    # ── Clic en el mapa ───────────────────────────────────────────────────────

    def _on_map_click(self, event):
        if event.inaxes != self.ax or event.button != 1:
            return
        if self.contexto_3857 is None or self.mun_interes is None:
            return
        # Ignorar clics cuando la barra de herramientas está en modo zoom/pan
        if self.canvas.toolbar and self.canvas.toolbar.mode:
            return

        pt  = Point(event.xdata, event.ydata)
        hit = self.contexto_3857[self.contexto_3857.geometry.contains(pt)]
        if hit.empty:
            return

        row    = hit.iloc[0]
        cvegeo = row["CVEGEO"]
        if cvegeo == self.mun_interes.iloc[0]["CVEGEO"]:
            return  # clic sobre el municipio de interés

        if any(v["CVEGEO"] == cvegeo for v in self.vecinos_items):
            # Quitar
            self.vecinos_items = [v for v in self.vecinos_items if v["CVEGEO"] != cvegeo]
            for item in self.tabla.get_children():
                if self.tabla.item(item, "values")[0] == cvegeo:
                    self.tabla.delete(item)
                    break
            if not self.vecinos_items:
                self.btn_exportar.config(state="disabled")
                self.btn_quitar.config(state="disabled")
            self._set_status(
                f"Vecino eliminado: {row['NOMGEO']}. "
                f"Total: {len(self.vecinos_items)} municipio(s)."
            )
        else:
            # Agregar
            self.vecinos_items.append(
                {"CVEGEO": cvegeo, "NOM_ENT": row["NOM_ENT"], "NOMGEO": row["NOMGEO"]}
            )
            self.tabla.insert("", "end", values=(cvegeo, row["NOM_ENT"], row["NOMGEO"]))
            self.btn_exportar.config(state="normal")
            self._set_status(
                f"Vecino agregado: {row['NOMGEO']}, {row['NOM_ENT']}. "
                f"Total: {len(self.vecinos_items)} municipio(s)."
            )

        self._actualizar_mapa()

    # ── Agregar vecino vía dropdown ───────────────────────────────────────────

    def _agregar_vecino(self):
        if self.mun_interes is None or self.municipios is None:
            return
        nom_ent = self.cb_estado_vec.get()
        nomgeo  = self.cb_municipio_vec.get()
        if not nom_ent or not nomgeo:
            return

        filas = self.municipios[
            (self.municipios["NOM_ENT"] == nom_ent) &
            (self.municipios["NOMGEO"]  == nomgeo)
        ]
        if filas.empty:
            messagebox.showerror("Error", f"Municipio '{nomgeo}, {nom_ent}' no encontrado.")
            return

        cvegeo = filas["CVEGEO"].values[0]

        if cvegeo == self.mun_interes.iloc[0]["CVEGEO"]:
            messagebox.showwarning(
                "Aviso", "Ese municipio es el de interés; no puede ser vecino de sí mismo."
            )
            return
        if any(v["CVEGEO"] == cvegeo for v in self.vecinos_items):
            messagebox.showwarning("Aviso", f"'{nomgeo}' ya está en la lista.")
            return

        self.vecinos_items.append(
            {"CVEGEO": cvegeo, "NOM_ENT": nom_ent, "NOMGEO": nomgeo}
        )
        self.tabla.insert("", "end", values=(cvegeo, nom_ent, nomgeo))
        self.btn_exportar.config(state="normal")
        self._actualizar_mapa()
        self._set_status(
            f"Vecino agregado: {nomgeo}, {nom_ent}. "
            f"Total: {len(self.vecinos_items)} municipio(s)."
        )

    # ── Quitar vecino vía tabla ───────────────────────────────────────────────

    def _quitar_vecino(self):
        sel = self.tabla.selection()
        if not sel:
            return
        item_id = sel[0]
        cvegeo  = self.tabla.item(item_id, "values")[0]
        self.tabla.delete(item_id)
        self.vecinos_items = [v for v in self.vecinos_items if v["CVEGEO"] != cvegeo]
        if not self.vecinos_items:
            self.btn_exportar.config(state="disabled")
            self.btn_quitar.config(state="disabled")
        self._actualizar_mapa()
        self._set_status(
            f"Vecino eliminado. Total: {len(self.vecinos_items)} municipio(s)."
        )

    def _on_tabla_seleccion(self, _event):
        if self.tabla.selection():
            self.btn_quitar.config(state="normal")

    def _vaciar_tabla(self):
        for row in self.tabla.get_children():
            self.tabla.delete(row)

    # ── Renderizado del mapa ──────────────────────────────────────────────────

    def _actualizar_mapa(self):
        self.ax.cla()
        self.ax.axis("off")

        CRS_VIZ        = "EPSG:3857"
        mun_plot       = self.mun_interes.to_crs(CRS_VIZ)
        mun_cvegeo     = self.mun_interes.iloc[0]["CVEGEO"]
        vecinos_cvegeos = {v["CVEGEO"] for v in self.vecinos_items}

        # ── Área de contexto (gris, con etiquetas y clic habilitado) ──────
        if self.contexto_3857 is not None:
            sin_selec = self.contexto_3857[
                ~self.contexto_3857["CVEGEO"].isin(vecinos_cvegeos | {mun_cvegeo})
            ]
            sin_selec.plot(
                ax=self.ax, color=COLOR_CONTEXTO, edgecolor=COLOR_CTX_EDGE,
                linewidth=0.5, alpha=0.9
            )
            for _, row in sin_selec.iterrows():
                self.ax.annotate(
                    row["NOMGEO"],
                    xy=(row.geometry.centroid.x, row.geometry.centroid.y),
                    fontsize=5.5, ha="center", va="center", color="#555",
                    bbox=dict(boxstyle="round,pad=0.1", fc="white", alpha=0.55, ec="none")
                )

        # ── Vecinos seleccionados (azul) ──────────────────────────────────
        if vecinos_cvegeos:
            vec_plot = self.municipios[
                self.municipios["CVEGEO"].isin(vecinos_cvegeos)
            ].to_crs(CRS_VIZ)
            vec_plot.plot(
                ax=self.ax, color=COLOR_VECINO,
                alpha=0.75, edgecolor="white", linewidth=0.8
            )
            for _, row in vec_plot.iterrows():
                self.ax.annotate(
                    row["NOMGEO"],
                    xy=(row.geometry.centroid.x, row.geometry.centroid.y),
                    fontsize=6, ha="center", va="center",
                    color="white", fontweight="bold",
                    bbox=dict(
                        boxstyle="round,pad=0.15", fc=COLOR_VECINO,
                        alpha=0.75, ec="none"
                    )
                )

        # ── Municipio de interés (rojo, encima de todo) ───────────────────
        mun_plot.plot(
            ax=self.ax, color=COLOR_INTERES,
            edgecolor="black", linewidth=1.0, zorder=5
        )

        try:
            ctx.add_basemap(
                self.ax, source=ctx.providers.CartoDB.Positron, zoom="auto"
            )
        except Exception:
            pass

        mun_row = self.mun_interes.iloc[0]
        leyenda = [
            mpatches.Patch(
                color=COLOR_INTERES,
                label=f"Municipio de interés: {mun_row['NOMGEO']}"
            ),
        ]
        if vecinos_cvegeos:
            leyenda.append(mpatches.Patch(
                color=COLOR_VECINO,
                label=f"Vecinos seleccionados ({len(vecinos_cvegeos)})"
            ))
        if self.contexto_3857 is not None:
            leyenda.append(mpatches.Patch(
                facecolor=COLOR_CONTEXTO, edgecolor=COLOR_CTX_EDGE,
                label="Clic para agregar / quitar vecino"
            ))
        self.ax.legend(handles=leyenda, loc="lower left", fontsize=8, framealpha=0.9)
        self.ax.set_title(
            f"{mun_row['NOMGEO']}, {mun_row['NOM_ENT']}  "
            f"(CVEGEO: {mun_row['CVEGEO']})",
            fontsize=10, fontweight="bold", pad=8
        )
        self.fig.tight_layout()
        self.canvas.draw()

    # ── Exportación ───────────────────────────────────────────────────────────

    def _exportar(self):
        if self.mun_interes is None or not self.vecinos_items:
            return
        cvegeos = [v["CVEGEO"] for v in self.vecinos_items]
        vec_gdf = self.municipios[self.municipios["CVEGEO"].isin(cvegeos)].copy()
        ruta    = Path(self.ruta_salida.get())
        cvegeo  = self.mun_interes.iloc[0]["CVEGEO"]
        try:
            gpkg, csv = exportar(self.mun_interes, vec_gdf, ruta, cvegeo)
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

    def destroy(self):
        plt.close(self.fig)
        super().destroy()


# ── Punto de entrada ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
