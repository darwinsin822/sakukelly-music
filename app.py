#!/usr/bin/env python3
import sys, sqlite3, random, os, json, time, hashlib, shutil, subprocess, re
from pathlib import Path
from PyQt6.QtCore import Qt, QUrl, QSize, QProcess, QTimer, QMimeData, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QPixmap, QColor, QPainter, QLinearGradient, QIcon, QDrag, QPen
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt6.QtWidgets import *

try:
    from mutagen import File as MF
except Exception:
    MF = None

HOME = Path.home()/".local/share/sakukelly"
HOME.mkdir(parents=True, exist_ok=True)
EXT={".mp3",".flac",".wav",".ogg",".m4a",".aac",".opus",".wma",".ape",".mka"}
SETTINGS_FILE = HOME/"settings.json"
APP_ID = "sakukelly"
APP_NAME = "Sakukelly Music"
APP_ICON = "/usr/share/icons/hicolor/scalable/apps/sakukelly.svg"
YTDLP = "/usr/lib/sakukelly/bin/yt-dlp"
FFMPEG = "/usr/lib/sakukelly/bin/ffmpeg"
YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")

def youtube_thumb_url(video_id):
    """URL de miniatura estable de YouTube a partir del id de vídeo.

    yt-dlp con --flat-playlist no siempre entrega un campo "thumbnail" plano
    (a veces solo entrega "thumbnails", una lista, o nada). Esta URL de
    i.ytimg.com funciona para prácticamente cualquier vídeo público sin
    depender de esos datos, así que sirve como respaldo fiable para que el
    streaming siempre tenga portada, igual que la música local.
    """
    vid = str(video_id or "").strip()
    if not YT_ID_RE.match(vid):
        return ""
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

def resolve_thumb(entry, video_id=None):
    """Devuelve la mejor URL de portada disponible para un resultado de YouTube."""
    thumb = str((entry or {}).get("thumbnail") or "") if isinstance(entry, dict) else str(entry or "")
    if thumb.startswith("http"):
        return thumb
    if isinstance(entry, dict):
        thumbs = entry.get("thumbnails") or []
        if isinstance(thumbs, list) and thumbs:
            last = thumbs[-1]
            if isinstance(last, dict) and str(last.get("url") or "").startswith("http"):
                return str(last["url"])
    vid = video_id if video_id else ((entry or {}).get("id") if isinstance(entry, dict) else None)
    return youtube_thumb_url(vid)

COVER_CACHE=HOME/"covers"
COVER_CACHE.mkdir(parents=True,exist_ok=True)

def embedded_or_folder_cover(path,size=150):
    """Load embedded artwork first; then common cover files beside the song."""
    try:
        key=hashlib.sha1(str(path).encode("utf-8","ignore")).hexdigest()
        cache=COVER_CACHE/(key+".png")
        if cache.exists():
            px=QPixmap(str(cache))
            if not px.isNull():
                return px.scaled(size,size,Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation)
        data=None;audio=None
        if MF:
            audio=MF(path)
            if audio and getattr(audio,"tags",None):
                for k in audio.tags.keys():
                    tag=audio.tags[k]
                    if str(k).startswith("APIC") and hasattr(tag,"data"):
                        data=tag.data;break
                if data is None and hasattr(audio,"pictures") and audio.pictures:
                    data=audio.pictures[0].data
                if data is None and "covr" in audio.tags:
                    data=bytes(audio.tags["covr"][0])
        if data is None and audio is not None:
            pics=getattr(audio,"pictures",None)
            if pics:data=getattr(pics[0],"data",None)
            tags=getattr(audio,"tags",None)
            if data is None and tags:
                for key in ("covr","metadata_block_picture","coverart"):
                    try:
                        val=tags.get(key)
                        if not val:continue
                        raw=val[0] if isinstance(val,(list,tuple)) else val
                        if key=="covr":data=bytes(raw)
                        elif isinstance(raw,str):
                            import base64
                            decoded=base64.b64decode(raw)
                            if key=="metadata_block_picture":
                                try:
                                    from mutagen.flac import Picture
                                    data=Picture(decoded).data
                                except Exception:data=decoded
                            else:data=decoded
                        elif isinstance(raw,(bytes,bytearray)):data=bytes(raw)
                        if data:break
                    except Exception:pass
        px=QPixmap()
        if data and px.loadFromData(data):
            px.save(str(cache),"PNG")
            return px.scaled(size,size,Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation)
        folder=Path(path).parent
        for name in ("cover.jpg","cover.jpeg","cover.png","cover.webp","folder.jpg","folder.jpeg","folder.png","front.jpg","front.jpeg","front.png","album.jpg","album.jpeg","album.png","artwork.jpg","artwork.png","AlbumArtSmall.jpg"):
            f=folder/name
            if f.exists():
                px=QPixmap(str(f))
                if not px.isNull():
                    return px.scaled(size,size,Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation)
    except Exception:
        pass
    return None

def artwork_for_rows(rows,size=150):
    for row in rows:
        px=embedded_or_folder_cover(row[0],size)
        if px is not None:
            return px
    return None

THEMES = {
 "noche":  {"deep":"#0d1233","mid":"#1a2050","accent":"#7c5cff","accent2":"#33e0ff","text":"#f2f3fb","dim":"#a9adcf"},
 "sunset": {"deep":"#2b0f2e","mid":"#4a1942","accent":"#ff6b6b","accent2":"#ffb347","text":"#fff3f3","dim":"#d6afc9"},
 "forest": {"deep":"#0b1f19","mid":"#123329","accent":"#3ddc97","accent2":"#9be15d","text":"#effff8","dim":"#9fc8b8"},
 "mono":   {"deep":"#0e0e10","mid":"#1c1c1f","accent":"#e5e5e5","accent2":"#8a8a8a","text":"#f5f5f5","dim":"#a0a0a0"},
 "candy":  {"deep":"#1a0b2e","mid":"#2e1065","accent":"#ff4fd8","accent2":"#4facfe","text":"#fff1fd","dim":"#c4acd9"},
}
class DB:
 def __init__(self):
  self.c=sqlite3.connect(HOME/"library.db")
  self.c.execute("""CREATE TABLE IF NOT EXISTS songs(path TEXT PRIMARY KEY,title TEXT,artist TEXT,album TEXT,genre TEXT,year TEXT,duration REAL,fav INTEGER DEFAULT 0,rating INTEGER DEFAULT 0)""")
  self.c.execute("CREATE TABLE IF NOT EXISTS folders(path TEXT PRIMARY KEY)")
  self.c.execute("CREATE TABLE IF NOT EXISTS playlists(name TEXT PRIMARY KEY)")
  self.c.execute("CREATE TABLE IF NOT EXISTS pl(name TEXT,path TEXT,pos INTEGER,PRIMARY KEY(name,path))")
  self.c.execute("CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT,path TEXT,played_at INTEGER,completed INTEGER DEFAULT 0)")
  self.c.execute("CREATE TABLE IF NOT EXISTS playstats(path TEXT PRIMARY KEY,plays INTEGER DEFAULT 0,skips INTEGER DEFAULT 0,last_played INTEGER DEFAULT 0,seconds INTEGER DEFAULT 0)")
  self.c.execute("CREATE TABLE IF NOT EXISTS streams(id TEXT PRIMARY KEY,title TEXT,artist TEXT,thumb TEXT,duration REAL DEFAULT 0,fav INTEGER DEFAULT 0)")
  self.c.execute("CREATE TABLE IF NOT EXISTS stream_pl(name TEXT,id TEXT,pos INTEGER,PRIMARY KEY(name,id))")
  self.c.execute("CREATE TABLE IF NOT EXISTS local_video(path TEXT PRIMARY KEY,video_id TEXT,title TEXT,artist TEXT,thumb TEXT,offset_ms INTEGER DEFAULT 0,updated_at INTEGER DEFAULT 0)")
  self.c.execute("CREATE TABLE IF NOT EXISTS downloads(id TEXT PRIMARY KEY,title TEXT,artist TEXT,path TEXT,downloaded_at INTEGER DEFAULT 0)")
  self.c.execute("CREATE TABLE IF NOT EXISTS stream_history(id INTEGER PRIMARY KEY AUTOINCREMENT,stream_id TEXT,played_at INTEGER)")

  self.c.commit()
 def tag(self,a,k,d=""):
  try:return (a.get(k) or [d])[0]
  except:return d
 def scan(self,f):
  self.c.execute("INSERT OR IGNORE INTO folders VALUES(?)",(f,));n=0
  for p in Path(f).rglob("*"):
   if not p.is_file() or p.suffix.lower() not in EXT:continue
   t,ar,al,g,y,d=p.stem,"Artista desconocido","Álbum desconocido","","",0
   if MF:
    try:
     a=MF(p,easy=True)
     if a:t=self.tag(a,"title",t);ar=self.tag(a,"artist",ar);al=self.tag(a,"album",al);g=self.tag(a,"genre");y=self.tag(a,"date");d=getattr(getattr(a,"info",None),"length",0) or 0
    except:pass
   self.c.execute("""INSERT INTO songs(path,title,artist,album,genre,year,duration) VALUES(?,?,?,?,?,?,?)
   ON CONFLICT(path) DO UPDATE SET title=excluded.title,artist=excluded.artist,album=excluded.album,genre=excluded.genre,year=excluded.year,duration=excluded.duration""",(str(p),t,ar,al,g,y,d));n+=1
  self.c.commit();return n
 def remove_folder(self,f):
  f=str(Path(f))
  # Remove only the library registration. Files on disk are never deleted.
  self.c.execute("DELETE FROM folders WHERE path=?",(f,))
  prefix=f.rstrip(os.sep)+os.sep+"%"
  self.c.execute("DELETE FROM songs WHERE path LIKE ?",(prefix,))
  self.c.commit()

 def songs(self,q="",fav=False):
  sql="SELECT path,title,artist,album,genre,year,duration,fav,rating FROM songs";a=[];w=[]
  if q:w.append("(title LIKE ? OR artist LIKE ? OR album LIKE ? OR genre LIKE ? OR year LIKE ?)");x=f"%{q}%";a=[x]*5
  if fav:w.append("fav=1")
  if w:sql+=" WHERE "+" AND ".join(w)
  return self.c.execute(sql+" ORDER BY artist COLLATE NOCASE,album COLLATE NOCASE,title COLLATE NOCASE",a).fetchall()
 def groups(self,col):
  if col not in ("artist","album","genre","year"):
   raise ValueError("Columna no permitida")
  return self.c.execute(f"SELECT {col},COUNT(*) FROM songs WHERE {col}<>'' GROUP BY {col} ORDER BY {col} COLLATE NOCASE").fetchall()
 def fav(self,p):self.c.execute("UPDATE songs SET fav=1-fav WHERE path=?",(p,));self.c.commit()
 def rate(self,p,n):self.c.execute("UPDATE songs SET rating=? WHERE path=?",(n,p));self.c.commit()
 def pls(self):return [x[0] for x in self.c.execute("SELECT name FROM playlists ORDER BY name")]
 def newpl(self,n):self.c.execute("INSERT OR IGNORE INTO playlists VALUES(?)",(n,));self.c.commit()
 def addpl(self,n,p):
  pos=self.c.execute("SELECT COUNT(*) FROM pl WHERE name=?",(n,)).fetchone()[0];self.c.execute("INSERT OR IGNORE INTO pl VALUES(?,?,?)",(n,p,pos));self.c.commit()
 def plsongs(self,n):return self.c.execute("""SELECT s.path,s.title,s.artist,s.album,s.genre,s.year,s.duration,s.fav,s.rating FROM pl JOIN songs s ON s.path=pl.path WHERE pl.name=? ORDER BY pl.pos""",(n,)).fetchall()


 def upsert_stream(self,r):
  self.c.execute("""INSERT INTO streams(id,title,artist,thumb,duration) VALUES(?,?,?,?,?)
  ON CONFLICT(id) DO UPDATE SET title=excluded.title,artist=excluded.artist,thumb=excluded.thumb,duration=excluded.duration""",
  (r.get("id",""),r.get("title",""),r.get("artist",""),r.get("thumb",""),r.get("duration",0)));self.c.commit()
 def stream(self,i):
  x=self.c.execute("SELECT id,title,artist,thumb,duration,fav FROM streams WHERE id=?",(i,)).fetchone()
  return {"id":x[0],"title":x[1],"artist":x[2],"thumb":x[3],"duration":x[4],"fav":x[5]} if x else None
 def stream_fav(self,i):
  self.c.execute("UPDATE streams SET fav=1-fav WHERE id=?",(i,));self.c.commit()
 def stream_favs(self):
  return self.c.execute("SELECT id,title,artist,thumb,duration,fav FROM streams WHERE fav=1 ORDER BY artist COLLATE NOCASE,title COLLATE NOCASE").fetchall()
 def add_stream_pl(self,n,i):
  pos=self.c.execute("SELECT COUNT(*) FROM stream_pl WHERE name=?",(n,)).fetchone()[0]
  self.c.execute("INSERT OR IGNORE INTO stream_pl VALUES(?,?,?)",(n,i,pos));self.c.commit()
 def stream_plsongs(self,n):
  return self.c.execute("""SELECT s.id,s.title,s.artist,s.thumb,s.duration,s.fav FROM stream_pl p JOIN streams s ON s.id=p.id WHERE p.name=? ORDER BY p.pos""",(n,)).fetchall()

 def local_video(self,path):
  x=self.c.execute("SELECT video_id,title,artist,thumb,offset_ms FROM local_video WHERE path=?",(path,)).fetchone()
  return {"id":x[0],"title":x[1],"artist":x[2],"thumb":x[3],"offset_ms":x[4]} if x else None
 def set_local_video(self,path,r,offset_ms=0):
  self.c.execute("""INSERT INTO local_video(path,video_id,title,artist,thumb,offset_ms,updated_at) VALUES(?,?,?,?,?,?,?)
  ON CONFLICT(path) DO UPDATE SET video_id=excluded.video_id,title=excluded.title,artist=excluded.artist,thumb=excluded.thumb,offset_ms=excluded.offset_ms,updated_at=excluded.updated_at""",
  (path,r.get("id",""),r.get("title",""),r.get("artist",""),r.get("thumb",""),int(offset_ms),int(time.time())));self.c.commit()
 def clear_local_video(self,path):
  self.c.execute("DELETE FROM local_video WHERE path=?",(path,));self.c.commit()
 def set_local_video_offset(self,path,offset_ms):
  self.c.execute("UPDATE local_video SET offset_ms=? WHERE path=?",(int(offset_ms),path));self.c.commit()

 def recent(self,limit=80):
  return self.c.execute("""SELECT s.path,s.title,s.artist,s.album,s.genre,s.year,s.duration,s.fav,s.rating
  FROM history h JOIN songs s ON s.path=h.path GROUP BY s.path ORDER BY MAX(h.played_at) DESC LIMIT ?""",(limit,)).fetchall()
 def add_stream_history(self,i):
  self.c.execute("INSERT INTO stream_history(stream_id,played_at) VALUES(?,?)",(i,int(time.time())));self.c.commit()
 def recent_streams(self,limit=40):
  return self.c.execute("""SELECT s.id,s.title,s.artist,s.thumb,s.duration,s.fav FROM stream_history h
  JOIN streams s ON s.id=h.stream_id GROUP BY s.id ORDER BY MAX(h.played_at) DESC LIMIT ?""",(limit,)).fetchall()
 def add_download(self,i,title,artist,path):
  self.c.execute("""INSERT INTO downloads(id,title,artist,path,downloaded_at) VALUES(?,?,?,?,?)
  ON CONFLICT(id) DO UPDATE SET title=excluded.title,artist=excluded.artist,path=excluded.path,downloaded_at=excluded.downloaded_at""",
  (i,title,artist,path,int(time.time())));self.c.commit()
 def downloads(self):
  rows=self.c.execute("SELECT id,title,artist,path,downloaded_at FROM downloads ORDER BY downloaded_at DESC").fetchall()
  return [r for r in rows if Path(r[3]).exists()]
 def remove_download_record(self,i):
  self.c.execute("DELETE FROM downloads WHERE id=?",(i,));self.c.commit()

 def played(self,p):
  now=int(time.time());self.c.execute("INSERT INTO history(path,played_at) VALUES(?,?)",(p,now))
  self.c.execute("""INSERT INTO playstats(path,plays,last_played) VALUES(?,1,?)
  ON CONFLICT(path) DO UPDATE SET plays=plays+1,last_played=excluded.last_played""",(p,now));self.c.commit()
 def skip(self,p):
  self.c.execute("""INSERT INTO playstats(path,skips) VALUES(?,1)
  ON CONFLICT(path) DO UPDATE SET skips=skips+1""",(p,));self.c.commit()
 def smart(self,kind):
  rows=self.songs();st={x[0]:x[1:] for x in self.c.execute("SELECT path,plays,skips,last_played FROM playstats")}
  if kind=="Más escuchadas":return sorted(rows,key=lambda r:st.get(r[0],(0,0,0))[0],reverse=True)[:100]
  if kind=="No escuchadas recientemente":return sorted(rows,key=lambda r:st.get(r[0],(0,0,0))[2])[:100]
  if kind=="Favoritas":return [r for r in rows if r[7]]
  if kind=="Mejor valoradas":return sorted(rows,key=lambda r:r[8],reverse=True)[:100]
  return rows
 def stats(self):
  return self.c.execute("SELECT COALESCE(SUM(plays),0),COALESCE(SUM(seconds),0) FROM playstats").fetchone(),self.c.execute("SELECT path,plays FROM playstats ORDER BY plays DESC LIMIT 10").fetchall()


def folder_rows(db, folder):
    root=Path(folder)
    return [r for r in db.songs() if Path(r[0]).parent == root]

def child_folders(db, folder):
    root=Path(folder); found=set()
    for r in db.songs():
        p=Path(r[0])
        try:
            rel=p.relative_to(root)
            if len(rel.parts)>1: found.add(str(root/rel.parts[0]))
        except ValueError:
            pass
    return sorted(found,key=str.casefold)

def gradient_pixmap(size, c1, c2):
    pm=QPixmap(size,size); pm.fill(Qt.GlobalColor.transparent)
    p=QPainter(pm); g=QLinearGradient(0,0,size,size); g.setColorAt(0,QColor(c1)); g.setColorAt(1,QColor(c2))
    p.setBrush(g); p.setPen(Qt.PenStyle.NoPen); p.drawRoundedRect(0,0,size,size,12,12); p.end()
    return pm


class DSPBackend:
    """Ecualización DSP real mediante PipeWire/PulseAudio.
    Crea un sink virtual LADSPA y enruta Sakukelly hacia él cuando el sistema
    dispone de module-ladspa-sink y del plugin mbeq_1197.so.
    """
    def __init__(self):
        self.module_id=None
        self.sink_name="sakukelly_eq"
        self.available=False
        self.pactl=shutil.which("pactl")
        self.plugin=self._find_plugin()
        self.available=bool(self.pactl and self.plugin)

    def _find_plugin(self):
        candidates=[
            "/usr/lib/ladspa/mbeq_1197.so",
            "/usr/lib/x86_64-linux-gnu/ladspa/mbeq_1197.so",
            "/usr/lib/aarch64-linux-gnu/ladspa/mbeq_1197.so",
        ]
        return next((x for x in candidates if Path(x).exists()), None)

    def _run(self,*args):
        try:
            return subprocess.run([self.pactl,*args],capture_output=True,text=True,timeout=4)
        except Exception:
            return None

    def unload(self):
        if self.module_id:
            self._run("unload-module",str(self.module_id))
            self.module_id=None

    def apply(self, bands):
        if not self.available:
            return False, "DSP no disponible"
        self.unload()
        # mbeq usa 15 bandas. Interpolamos las 8 del mockup a sus centros.
        src=[60,150,400,1000,2400,6000,12000,16000]
        dst=[50,100,156,220,311,440,622,880,1250,1750,2500,3500,5000,10000,20000]
        def interp(x):
            if x<=src[0]: return bands[0]
            if x>=src[-1]: return bands[-1]
            for i in range(len(src)-1):
                if src[i]<=x<=src[i+1]:
                    a=(x-src[i])/(src[i+1]-src[i])
                    return bands[i]*(1-a)+bands[i+1]*a
            return 0
        # LADSPA mbeq recibe ganancias lineales aproximadas por banda.
        vals=[10**(interp(x)/20.0) for x in dst]
        control=",".join(f"{v:.4f}" for v in vals)
        default_sink=self._run("get-default-sink")
        master=default_sink.stdout.strip() if default_sink and default_sink.returncode==0 else ""
        args=["load-module","module-ladspa-sink",
              f"sink_name={self.sink_name}",
              f"plugin={Path(self.plugin).stem}",
              "label=mbeq",
              f"control={control}"]
        if master: args.append(f"master={master}")
        r=self._run(*args)
        if not r or r.returncode!=0:
            return False, (r.stderr.strip() if r else "No se pudo cargar LADSPA")
        self.module_id=r.stdout.strip()
        return True, self.sink_name

class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent); self.parent=parent
        self.setWindowTitle("Configuración"); self.resize(700,590); self.setObjectName("settings")
        v=QVBoxLayout(self); title=QLabel("Configuración"); title.setObjectName("dialogTitle"); v.addWidget(title)
        v.addWidget(QLabel("Personaliza la apariencia y el sonido de Sakukelly"))
        v.addWidget(self.label("TEMAS DE COLOR"))
        themes=QHBoxLayout()
        for key,label in [("noche","Noche estrellada"),("sunset","Atardecer"),("forest","Bosque"),("mono","Monocromo"),("candy","Candy")]:
            b=QPushButton(label); b.setMinimumHeight(58); b.clicked.connect(lambda _,k=key:self.parent.apply_theme(k)); themes.addWidget(b)
        v.addLayout(themes)
        v.addWidget(self.label("ECUALIZADOR"))
        presets=QHBoxLayout()
        for n in ["Plano","Graves","Voces","Agudos","Rock"]:
            b=QPushButton(n); b.clicked.connect(lambda _,x=n:self.preset(x)); presets.addWidget(b)
        v.addLayout(presets)
        bands=QHBoxLayout(); self.eq=[]
        for hz in ["60","150","400","1K","2.4K","6K","12K","16K"]:
            col=QVBoxLayout(); sl=QSlider(Qt.Orientation.Vertical); sl.setRange(-12,12); sl.setValue(0); sl.setMinimumHeight(160)
            self.eq.append(sl); sl.valueChanged.connect(self.eq_changed); col.addWidget(sl); lab=QLabel(hz); lab.setAlignment(Qt.AlignmentFlag.AlignCenter); col.addWidget(lab); bands.addLayout(col)
        v.addLayout(bands)
        v.addWidget(self.label("FONDO PERSONALIZADO"))
        wallrow=QHBoxLayout()
        wb=QPushButton("Elegir imagen o wallpaper");wb.clicked.connect(self.choose_wallpaper);wallrow.addWidget(wb)
        wr=QPushButton("Quitar wallpaper");wr.clicked.connect(self.clear_wallpaper);wallrow.addWidget(wr)
        v.addLayout(wallrow)
        self.wallLabel=QLabel(self.parent.wallpaper if self.parent.wallpaper else "Fondo del tema actual")
        self.wallLabel.setWordWrap(True);v.addWidget(self.wallLabel)
        v.addWidget(self.label("PREFERENCIAS GENERALES"))
        self.normalize=QCheckBox("Normalizar volumen"); self.normalize.setChecked(True); v.addWidget(self.normalize)
        self.crossfade=QCheckBox("Crossfade entre canciones"); self.crossfade.setChecked(self.parent.crossfade); self.crossfade.toggled.connect(self.crossfade_changed); v.addWidget(self.crossfade)
        cfrow=QHBoxLayout(); cfrow.addWidget(QLabel("Duración del crossfade")); self.crossfadeSlider=QSlider(Qt.Orientation.Horizontal); self.crossfadeSlider.setRange(1,10); self.crossfadeSlider.setValue(max(1,min(10,self.parent.crossfade_ms//1000))); self.crossfadeSlider.valueChanged.connect(self.crossfade_duration_changed); cfrow.addWidget(self.crossfadeSlider,1); self.crossfadeValue=QLabel(f"{self.crossfadeSlider.value()} s"); cfrow.addWidget(self.crossfadeValue); v.addLayout(cfrow)
        saved=self.parent.eq_values
        for sl,val in zip(self.eq,saved): sl.blockSignals(True); sl.setValue(int(val)); sl.blockSignals(False)
        self.dspStatus=QLabel("DSP: comprobando…"); v.addWidget(self.dspStatus)
        self.eqTimer=QTimer(self); self.eqTimer.setSingleShot(True); self.eqTimer.setInterval(180); self.eqTimer.timeout.connect(self.commit_eq)
        self.commit_eq()
        mini=QPushButton("Abrir Player");mini.setToolTip("Muestra la carátula y controles de la canción actual");mini.clicked.connect(self.open_mini);v.addWidget(mini)
        close=QPushButton("Guardar y cerrar"); close.setObjectName("primary"); close.clicked.connect(self.accept); v.addWidget(close,0,Qt.AlignmentFlag.AlignRight)
    def label(self,t):
        x=QLabel(t); x.setObjectName("eyebrow"); return x
    def choose_wallpaper(self):
        f,_=QFileDialog.getOpenFileName(self,"Seleccionar wallpaper","","Imágenes (*.jpg *.jpeg *.png *.webp *.bmp)")
        if f:
            self.parent.wallpaper=f
            self.parent.save_settings()
            self.parent.apply_wallpaper()
            self.wallLabel.setText(f)
    def clear_wallpaper(self):
        self.parent.wallpaper=""
        self.parent.save_settings()
        self.parent.apply_wallpaper()
        self.wallLabel.setText("Fondo del tema actual")
    def crossfade_changed(self,on):
        self.parent.crossfade=bool(on); self.parent.save_settings()
    def crossfade_duration_changed(self,value):
        self.parent.crossfade_ms=int(value)*1000; self.crossfadeValue.setText(f"{value} s"); self.parent.save_settings()
    def open_download_manager(self):
        dlg=QDialog(self);dlg.setWindowTitle("Descargas y almacenamiento");dlg.resize(680,520)
        v=QVBoxLayout(dlg);h=QLabel("Música disponible sin conexión");h.setObjectName("heading");v.addWidget(h)
        info=QLabel("Las descargas se administran aquí para mantener limpia la navegación principal.");info.setWordWrap(True);v.addWidget(info)
        lst=QListWidget();v.addWidget(lst,1)
        rows=self.d.downloads()
        total=0
        for i,title,artist,path,ts in rows:
            try:total+=Path(path).stat().st_size
            except Exception:pass
            it=QListWidgetItem(f"⇩  {title}\n    {artist}\n    {path}");it.setData(Qt.ItemDataRole.UserRole,(i,path));lst.addItem(it)
        foot=QLabel(f"{len(rows)} descargas · {total/1024/1024:.1f} MB");v.addWidget(foot)
        buttons=QHBoxLayout();openb=QPushButton("Reproducir");remove=QPushButton("Quitar de la lista");close=QPushButton("Cerrar")
        buttons.addWidget(openb);buttons.addWidget(remove);buttons.addStretch();buttons.addWidget(close);v.addLayout(buttons)
        def play():
            it=lst.currentItem()
            if not it:return
            _,path=it.data(Qt.ItemDataRole.UserRole)
            if Path(path).exists():
                self.stream_current=None;self.local_current={"path":path,"title":it.text().split("\n")[0].replace("⇩  ",""),"artist":"","album":"Descargas"}
                self.p.setSource(QUrl.fromLocalFile(path));self.p.play();dlg.accept()
        def forget():
            it=lst.currentItem()
            if not it:return
            i,_=it.data(Qt.ItemDataRole.UserRole);self.d.remove_download_record(i);lst.takeItem(lst.row(it))
        openb.clicked.connect(play);remove.clicked.connect(forget);close.clicked.connect(dlg.accept);dlg.exec()

    def open_mini(self):
        self.parent.open_video_player()
    def preset(self,n):
        values={"Plano":[0]*8,"Graves":[8,6,4,1,0,-1,-2,-2],"Voces":[-2,-1,2,5,5,3,1,0],"Agudos":[-3,-2,-1,0,2,5,7,8],"Rock":[5,3,-2,-3,1,3,5,6]}[n]
        for sl,v in zip(self.eq,values): sl.setValue(v)
        self.commit_eq()
    def eq_changed(self,_=None):
        self.eqTimer.start()
    def commit_eq(self):
        vals=[sl.value() for sl in self.eq]
        ok,msg=self.parent.set_equalizer(vals)
        self.dspStatus.setText("DSP activo · ecualización aplicada en tiempo real" if ok else "DSP no activo · instala swh-plugins y asegúrate de tener pactl")




class WaveformWidget(QWidget):
    """Decorative live-looking waveform for the main player."""
    def __init__(self,parent=None):
        super().__init__(parent);self.setObjectName("waveform");self.setFixedHeight(58);self.phase=0
        self.timer=QTimer(self);self.timer.setInterval(90);self.timer.timeout.connect(self.tick);self.timer.start()
    def tick(self):
        self.phase=(self.phase+1)%1000;self.update()
    def paintEvent(self,event):
        pa=QPainter(self);pa.setRenderHint(QPainter.RenderHint.Antialiasing)
        w,h=self.width(),self.height();cy=h/2;bars=43;gap=3.0;bw=max(2.0,(w-gap*(bars-1))/bars)
        playing=False
        win=self.window()
        try: playing=win.p.playbackState()==QMediaPlayer.PlaybackState.PlayingState
        except Exception: pass
        accent=QColor("#ff4fc3");white=QColor(235,238,250,220)
        for i in range(bars):
            x=i*(bw+gap);d=abs(i-(bars-1)/2)/((bars-1)/2)
            env=max(.14,1-d*.88);wave=(.48+.52*abs(__import__('math').sin(i*.67+self.phase*.12))) if playing else (.38+.34*abs(__import__('math').sin(i*.55)))
            bh=max(4,h*env*wave);c=white if i<bars*.42 else accent
            pa.setPen(QPen(c,bw,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap));pa.drawLine(int(x+bw/2),int(cy-bh/2),int(x+bw/2),int(cy+bh/2))

class TrackListWidget(QListWidget):
    """List optimized for mouse and touch drag-to-player."""
    def startDrag(self,supportedActions):
        it=self.currentItem()
        if not it:return
        data=it.data(Qt.ItemDataRole.UserRole)
        mime=QMimeData();mime.setText("sakukelly:"+json.dumps(data,ensure_ascii=False))
        drag=QDrag(self);drag.setMimeData(mime);drag.exec(Qt.DropAction.CopyAction)

class DropPlayerFrame(QFrame):
    dropped=pyqtSignal(object)
    def __init__(self,parent=None):
        super().__init__(parent);self.setAcceptDrops(True)
    def dragEnterEvent(self,e):
        md=e.mimeData()
        if md.hasUrls() or (md.hasText() and md.text().startswith("sakukelly:")):e.acceptProposedAction()
        else:e.ignore()
    def dropEvent(self,e):
        md=e.mimeData()
        if md.hasUrls():
            files=[u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if files:self.dropped.emit(("files",files));e.acceptProposedAction();return
        if md.hasText() and md.text().startswith("sakukelly:"):
            try:self.dropped.emit(("item",json.loads(md.text()[11:])));e.acceptProposedAction();return
            except Exception:pass
        e.ignore()

class Win(QMainWindow):
    def __init__(self):
        super().__init__(); self.d=DB(); self.rows=[]; self.i=-1; self.queue=[]; self.view="Inicio"; self.filter=None
        self.shuffle=False; self.repeat=False; self.currentFolder=None; self.theme="noche"
        self.stream_rows=[]; self.stream_current=None; self.local_current=None; self.video_player=None; self.stream_process=None; self.resolve_process=None; self.download_process=None; self.hybrid_process=None; self.hybrid_token=0; self.net=QNetworkAccessManager(self)
        self.stream_search_timer=QTimer(self); self.stream_search_timer.setSingleShot(True); self.stream_search_timer.setInterval(650); self.stream_search_timer.timeout.connect(self.search_youtube)
        self.deezer_timer=QTimer(self);self.deezer_timer.setSingleShot(True);self.deezer_timer.setInterval(800);self.deezer_timer.timeout.connect(self.search_deezer)
        self.load_settings()
        self.dsp=DSPBackend()
        self.setWindowTitle(APP_NAME); self.setWindowIcon(QIcon(APP_ICON)); self.resize(1450,880); self.setMinimumSize(900,560)
        self.au=QAudioOutput(); self.au.setVolume(.78); self.base_volume=.78; self.p=QMediaPlayer(); self.p.setAudioOutput(self.au)
        # Reproductor de sombra: solo se usa para solapar audio durante un crossfade real.
        self.au2=QAudioOutput(); self.au2.setVolume(0.0); self.p2=QMediaPlayer(); self.p2.setAudioOutput(self.au2)
        self.crossfade_started=False; self.fadeTimer=QTimer(self); self.fadeTimer.setInterval(40); self.fadeTimer.timeout.connect(self._fade_step); self.fadeStart=0
        self._pending_next=None

        base=QWidget(); base.setObjectName("base"); self.setCentralWidget(base); outer=QVBoxLayout(base); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)

        # Topbar del mockup
        top=QFrame(); top.setObjectName("topbar"); top.setFixedHeight(66); th=QHBoxLayout(top); th.setContentsMargins(18,8,24,8)
        topBrand=QLabel("♪  Sakukelly Music"); topBrand.setObjectName("topBrand"); topBrand.setMinimumWidth(255); th.addWidget(topBrand)
        th.addStretch()
        self.search=QLineEdit(); self.search.setObjectName("search"); self.search.setPlaceholderText("⌕  ¿Qué quieres reproducir?"); self.search.setMaximumWidth(450); self.search.setMinimumWidth(320)
        self.search.textChanged.connect(self.search_changed); self.search.returnPressed.connect(self.search_youtube); th.addWidget(self.search,1); th.addStretch()
        cfg=QPushButton("⚙"); cfg.setObjectName("circle"); cfg.clicked.connect(self.settings); th.addWidget(cfg)
        self.avatar=QLabel("D"); self.avatar.setObjectName("avatar"); self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter); self.avatar.setFixedSize(40,40); th.addWidget(self.avatar)
        outer.addWidget(top)

        body=QHBoxLayout(); body.setSpacing(0); outer.addLayout(body,1)

        # Biblioteca lateral, como el mockup
        side=QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(190); sv=QVBoxLayout(side); sv.setContentsMargins(12,16,10,14); sv.setSpacing(5)
        lib=QLabel("TU BIBLIOTECA"); lib.setObjectName("eyebrow"); sv.addWidget(lib)
        for icon,n in [("⌂","Inicio"),("◉","Streaming"),("⇩","Offline"),("♥","Favoritos"),("♫","Canciones"),("▤","Carpetas"),("▣","Álbumes"),("♟","Artistas"),("≡","Géneros"),("◫","Playlists")]:
            b=QPushButton(f"{icon}   {n}"); b.setObjectName("nav"); b.clicked.connect(lambda _,x=n:self.nav(x)); sv.addWidget(b)
        sv.addStretch(1)
        add=QPushButton("＋ Añadir música"); add.setObjectName("primary"); add.clicked.connect(self.folder); sv.addWidget(add)
        body.addWidget(side)

        # Contenido central
        main=QWidget(); mv=QVBoxLayout(main); mv.setContentsMargins(20,16,18,12); mv.setSpacing(10)
        self.heading=QLabel("Elegidos para ti"); self.heading.setObjectName("heading"); mv.addWidget(self.heading)
        self.hero=QFrame(); self.hero.setObjectName("hero"); hh=QHBoxLayout(self.hero)
        self.folderText=QLabel("Tu música, a tu manera"); self.folderText.setObjectName("heroText"); hh.addWidget(self.folderText,1)
        rs=QPushButton("↻ Reescanear"); rs.clicked.connect(self.rescan); hh.addWidget(rs); mv.addWidget(self.hero)

        self.cards=QListWidget(); self.cards.setObjectName("cards"); self.cards.setViewMode(QListView.ViewMode.IconMode); self.cards.setResizeMode(QListView.ResizeMode.Adjust)
        self.cards.setMovement(QListView.Movement.Static); self.cards.setIconSize(QSize(150,150)); self.cards.setGridSize(QSize(174,202)); self.cards.setMinimumHeight(215); self.cards.setMaximumHeight(228); self.cards.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.cards.itemClicked.connect(self.card_activate);mv.addWidget(self.cards)

        self.section=QLabel("Descubre"); self.section.setObjectName("sectionTitle"); mv.addWidget(self.section)
        self.list=TrackListWidget(); self.list.setObjectName("tracks"); self.list.itemClicked.connect(lambda _=None:self.activate()); self.list.setDragEnabled(True)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.list.customContextMenuRequested.connect(self.menu)
        self.list.model().rowsMoved.connect(self.queue_reordered); mv.addWidget(self.list,1)
        body.addWidget(main,1)

        # Player principal derecho: siempre visible, sin ocupar el borde inferior.
        player=DropPlayerFrame(); player.setObjectName("player"); player.setFixedWidth(340); player.dropped.connect(self.player_drop)
        ph=QVBoxLayout(player); ph.setContentsMargins(16,18,16,16); ph.setSpacing(10)
        pt=QLabel("AHORA SUENA");pt.setObjectName("eyebrow");ph.addWidget(pt)
        self.cover=QLabel("♪"); self.cover.setObjectName("cover"); self.cover.setFixedSize(300,300)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter); ph.addWidget(self.cover,0,Qt.AlignmentFlag.AlignHCenter)
        self.wave=WaveformWidget(self);ph.addWidget(self.wave)
        self.now=QLabel("Nada reproduciéndose\nBiblioteca local"); self.now.setObjectName("now")
        self.now.setWordWrap(True);self.now.setAlignment(Qt.AlignmentFlag.AlignCenter);ph.addWidget(self.now)
        actions=QHBoxLayout();actions.addStretch()
        fav=QPushButton("♡");fav.setObjectName("circle");fav.setToolTip("Favorito");fav.clicked.connect(self.favorite);actions.addWidget(fav)
        dl=QPushButton("⇩");dl.setObjectName("circle");dl.setToolTip("Descargar contenido autorizado");dl.clicked.connect(self.download_current_stream);actions.addWidget(dl)
        mini=QPushButton("▣");mini.setObjectName("circle");mini.setToolTip("Abrir mini-player");mini.clicked.connect(self.open_video_player);actions.addWidget(mini)
        yt=QPushButton("▶Y");yt.setObjectName("circle");yt.setToolTip("Ver vídeo en YouTube");yt.clicked.connect(self.open_current_on_youtube);actions.addWidget(yt)
        actions.addStretch();ph.addLayout(actions)
        buttons=QHBoxLayout();buttons.addStretch()
        sh=QPushButton("⤨");sh.setObjectName("circle");sh.setCheckable(True);sh.setToolTip("Aleatorio");sh.toggled.connect(lambda x:setattr(self,"shuffle",x));buttons.addWidget(sh)
        pr=QPushButton("◀◀");pr.setObjectName("circle");pr.clicked.connect(self.prev);buttons.addWidget(pr)
        self.play=QPushButton("▶");self.play.setObjectName("play");self.play.clicked.connect(self.toggle);buttons.addWidget(self.play)
        nx=QPushButton("▶▶");nx.setObjectName("circle");nx.clicked.connect(self.next);buttons.addWidget(nx)
        rp=QPushButton("↻");rp.setObjectName("circle");rp.setCheckable(True);rp.setToolTip("Repetir");rp.toggled.connect(lambda x:setattr(self,"repeat",x));buttons.addWidget(rp)
        buttons.addStretch();ph.addLayout(buttons)
        timeline=QHBoxLayout();self.tm=QLabel("0:00");timeline.addWidget(self.tm)
        self.sl=QSlider(Qt.Orientation.Horizontal);self.sl.sliderMoved.connect(self.p.setPosition);timeline.addWidget(self.sl,1)
        self.tt=QLabel("0:00");timeline.addWidget(self.tt);ph.addLayout(timeline)
        volume=QHBoxLayout();volume.addWidget(QLabel("🔊"));vol=QSlider(Qt.Orientation.Horizontal);vol.setRange(0,100);vol.setValue(78);vol.valueChanged.connect(self.set_volume);volume.addWidget(vol,1);ph.addLayout(volume)
        ph.addStretch(1)
        body.addWidget(player)
        self.p.positionChanged.connect(self.pos); self.p.durationChanged.connect(self.sl.setMaximum); self.p.mediaStatusChanged.connect(self.end)
        self.p.playbackStateChanged.connect(lambda s:self.play.setText("Ⅱ" if s==QMediaPlayer.PlaybackState.PlayingState else "▶"))
        self.apply_theme(self.theme, save=False); self.reload_playlists(); self.nav("Inicio"); QTimer.singleShot(350,self.restore_session)

    def label(self,t):
        x=QLabel(t); x.setObjectName("eyebrow"); return x

    def load_settings(self):
        try:
            data=json.loads(SETTINGS_FILE.read_text())
            self.theme=data.get("theme","noche")
            self.eq_values=data.get("eq",[0,0,0,0,0,0,0,0]); self.wallpaper=data.get("wallpaper",""); self.crossfade=data.get("crossfade",True); self.crossfade_ms=int(data.get("crossfade_ms",4000))
        except Exception:
            self.theme="noche"; self.eq_values=[0,0,0,0,0,0,0,0]; self.wallpaper=""; self.crossfade=True; self.crossfade_ms=4000
    def save_settings(self):
        try:
            old={}
            if SETTINGS_FILE.exists():
                try:old=json.loads(SETTINGS_FILE.read_text())
                except Exception:old={}
            old.update({"theme":self.theme,"eq":self.eq_values,"wallpaper":self.wallpaper,"crossfade":self.crossfade,"crossfade_ms":self.crossfade_ms})
            SETTINGS_FILE.write_text(json.dumps(old))
        except Exception: pass
    def set_equalizer(self,values):
        self.eq_values=list(values); self.save_settings()
        ok,msg=self.dsp.apply(self.eq_values)
        if ok:
            # QtMultimedia crea su stream dinámicamente. Se mueve el stream de Sakukelly
            # al sink DSP cuando aparece; se repite unos instantes para cubrir play/pause.
            self.route_timer=QTimer(self); self.route_timer.timeout.connect(self.route_to_dsp)
            self.route_timer.start(350); QTimer.singleShot(3500,self.route_timer.stop)
            self.route_to_dsp()
        return ok,msg
    def route_to_dsp(self):
        if not self.dsp.available or not self.dsp.module_id:return
        try:
            r=subprocess.run(["pactl","list","sink-inputs"],capture_output=True,text=True,timeout=3).stdout
            blocks=r.split("Sink Input #")[1:]
            for b in blocks:
                first=b.splitlines()[0].strip()
                low=b.lower()
                if "sakukell" in low or "python" in low or "qt" in low:
                    subprocess.run(["pactl","move-sink-input",first,self.dsp.sink_name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except Exception:pass

    def apply_theme(self,name,save=True):
        self.theme=name if name in THEMES else "noche"; t=THEMES[self.theme]
        self.setStyleSheet(f"""
        QWidget#base{{background:qradialgradient(cx:.5,cy:0,radius:1,fx:.5,fy:0,stop:0 {t['mid']},stop:1 {t['deep']});color:{t['text']};font-family:'Segoe UI','Noto Sans',sans-serif;font-size:14px}}
        QWidget{{color:{t['text']}}}
        #topbar{{background:rgba(255,255,255,0.035);border-bottom:1px solid rgba(255,255,255,.07)}} #brand{{font-size:19px;font-weight:800}} #brandDot{{color:{t['accent']};font-size:22px}}
        #sidebar{{background:rgba(5,7,18,.72);border-right:1px solid rgba(255,255,255,.075)}} #topBrand{{font-size:20px;font-weight:800;padding:4px 10px;color:{t['text']}}} #eyebrow{{color:{t['accent2']};font-size:11px;font-weight:800;letter-spacing:1px;margin:6px 8px}}
        #nav{{text-align:left;background:transparent;border:0;border-radius:11px;padding:8px 12px;color:{t['text']}}} #nav:hover{{background:rgba(255,255,255,.10);border-left:2px solid {t['accent']}}}
        #search{{background:rgba(255,255,255,.065);border:1px solid rgba(255,255,255,.10);border-radius:23px;padding:11px 17px;color:{t['text']}}} #search:focus{{border:1px solid {t['accent']}}}
        #avatar{{background:{t['accent']};border-radius:20px;font-weight:800}} #heading{{font-size:27px;font-weight:800}} #sectionTitle{{font-size:21px;font-weight:800}}
        #hero{{background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.07);border-radius:16px}} #heroText{{font-size:15px;font-weight:600}}
        QPushButton{{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.07);border-radius:17px;padding:8px 13px}} QPushButton:hover{{background:rgba(255,255,255,.12)}} QPushButton:checked{{background:{t['accent']}}}
        #primary{{background:{t['accent']};font-weight:700}} #circle{{min-width:38px;min-height:38px;border-radius:19px;padding:0}} #play{{background:{t['text']};color:{t['deep']};min-width:42px;min-height:42px;border-radius:21px;font-size:18px}}
        #cards{{background:transparent;border:0;outline:0}} #cards::item{{background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.055);border-radius:15px;padding:9px}} #cards::item:hover{{background:rgba(255,255,255,.12)}} #cards::item:selected{{background:rgba(255,255,255,.14)}}
        #tracks,#miniList{{background:rgba(7,9,24,.46);border:1px solid rgba(255,255,255,.055);border-radius:14px;outline:0}} #tracks::item,#miniList::item{{padding:10px;border-radius:10px}} #tracks::item:hover,#miniList::item:hover{{background:rgba(255,255,255,.08)}} #tracks::item:selected,#miniList::item:selected{{background:{t['accent']}}}
        #player{{background:rgba(12,10,26,.90);border-left:1px solid rgba(255,255,255,.10);border-radius:24px 0 0 24px}} #cover{{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.10);border-radius:22px;font-size:42px}} #waveform{{background:transparent;border:0}} #now{{font-weight:700;font-size:15px;padding:4px}} #player QPushButton#circle{{min-width:42px;min-height:42px;border-radius:21px;background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.12)}} #player QPushButton#circle:hover{{background:rgba(255,255,255,.13)}} #player QPushButton#play{{min-width:62px;min-height:62px;border-radius:31px;border:1px solid rgba(255,255,255,.28);font-size:20px}}
        QSlider::groove:horizontal{{height:4px;background:rgba(255,255,255,.16);border-radius:2px}} QSlider::handle:horizontal{{background:{t['accent2']};width:12px;margin:-4px 0;border-radius:6px}}
        QMenu,QDialog{{background:{t['mid']};color:{t['text']}}} #dialogTitle{{font-size:23px;font-weight:800}}
        """)
        if save: self.save_settings()
        self.apply_wallpaper(); self.populate_cards()

    def apply_wallpaper(self):
        if self.wallpaper and Path(self.wallpaper).exists():
            # Escape backslashes and quotes so a path can't break out of the
            # CSS string or inject additional style rules.
            safe=self.wallpaper.replace("\\","/").replace('"','\\"')
            self.centralWidget().setStyleSheet(f'QWidget#base{{border-image:url("{safe}") 0 0 0 0 stretch stretch;}}')
        else:
            self.centralWidget().setStyleSheet("")
    def open_queue_panel(self):
        dlg=QDialog(self);dlg.setWindowTitle("A continuación");dlg.resize(460,620);dlg.setObjectName("queueDialog")
        v=QVBoxLayout(dlg);v.setContentsMargins(18,18,18,16)
        h=QLabel("A continuación");h.setObjectName("dialogTitle");v.addWidget(h)
        sub=QLabel("Arrastra las canciones para cambiar el orden.");sub.setObjectName("eyebrow");v.addWidget(sub)
        lst=QListWidget();lst.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove);v.addWidget(lst,1)
        def fill():
            lst.clear()
            if self.local_current or self.stream_current:
                r=self.local_current or self.stream_current
                now=QListWidgetItem(f"▮▮  REPRODUCIENDO\n{r.get('title','')}\n{r.get('artist','')}")
                now.setFlags(Qt.ItemFlag.NoItemFlags);lst.addItem(now)
            for idx,entry in enumerate(self.queue):
                kind=entry[0] if isinstance(entry,tuple) else "local";payload=entry[1] if isinstance(entry,tuple) else entry
                if kind=="stream":
                    r=self.d.stream(payload);title=r["title"] if r else str(payload);artist=r["artist"] if r else ""
                else:
                    r=next((x for x in self.d.songs() if x[0]==payload),None);title=r[1] if r else Path(str(payload)).name;artist=r[2] if r else ""
                it=QListWidgetItem(f"{idx+1:02d}  {title}\n     {artist}");it.setData(Qt.ItemDataRole.UserRole,("q",idx));lst.addItem(it)
        def moved(*_):
            ordered=[]
            for n in range(lst.count()):
                d=lst.item(n).data(Qt.ItemDataRole.UserRole)
                if isinstance(d,tuple) and d[0]=="q" and d[1]<len(self.queue):ordered.append(self.queue[d[1]])
            if len(ordered)==len(self.queue):self.queue=ordered;QTimer.singleShot(0,fill)
        lst.model().rowsMoved.connect(moved)
        row=QHBoxLayout();remove=QPushButton("Quitar");clear=QPushButton("Vaciar cola");close=QPushButton("Cerrar")
        def rm():
            it=lst.currentItem();d=it.data(Qt.ItemDataRole.UserRole) if it else None
            if isinstance(d,tuple) and d[0]=="q" and d[1]<len(self.queue):self.queue.pop(d[1]);fill()
        remove.clicked.connect(rm);clear.clicked.connect(lambda:(self.queue.clear(),fill()));close.clicked.connect(dlg.accept)
        row.addWidget(remove);row.addWidget(clear);row.addStretch();row.addWidget(close);v.addLayout(row);fill();dlg.exec()

    def settings(self):
        SettingsDialog(self).exec()

    def reload_playlists(self):
        # Las playlists se muestran en su vista principal; sin mini-lista lateral.
        return

    def open_mini_playlist(self,it):
        self.view="Playlists"; self.filter=("playlist",it.data(Qt.ItemDataRole.UserRole)); self.search.clear(); self.heading.setText(self.filter[1]); self.refresh()

    def folder(self):
        f=QFileDialog.getExistingDirectory(self,"Selecciona tu carpeta de música")
        if f:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try: n=self.d.scan(f)
            finally: QApplication.restoreOverrideCursor()
            self.folderText.setText(f); self.reload_playlists(); self.refresh(); self.populate_cards()
            self.statusBar().showMessage(f"Escaneo completado: {n} archivos",4500)

    def rescan(self):
        fs=[x[0] for x in self.d.c.execute("SELECT path FROM folders")]; n=0
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for f in fs:
                if Path(f).exists(): n+=self.d.scan(f)
        finally: QApplication.restoreOverrideCursor()
        self.refresh(); self.populate_cards(); self.statusBar().showMessage(f"Biblioteca actualizada: {n} archivos revisados",4500)

    def nav(self,n):
        self.view=n; self.filter=None
        self.stream_search_timer.stop()
        if n!="Carpetas": self.currentFolder=None
        self.search.blockSignals(True); self.search.clear(); self.search.blockSignals(False)
        self.heading.setText("Para ti" if n=="Inicio" else ("YouTube · Streaming" if n=="Streaming" else n))
        self.hero.setVisible(n in ("Inicio","Canciones","Carpetas"))
        self.cards.setVisible(n in ("Inicio","Álbumes","Artistas","Géneros"))
        self.populate_cards()
        if n=="Streaming":
            self.cards.hide(); self.list.clear(); self.rows=[]; self.stream_rows=[]
            self.section.setText("Busca canciones, artistas o álbumes")
            it=QListWidgetItem("Escribe en el buscador superior para escuchar música desde YouTube\nLa descarga está disponible para contenido que tengas derecho a guardar.")
            it.setFlags(Qt.ItemFlag.NoItemFlags); self.list.addItem(it); return
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove if n=="Cola" else QAbstractItemView.DragDropMode.NoDragDrop)
        if n=="Cola":
            self.cards.hide(); self.rows=[]
            self.section.setText("Arrastra para ordenar · lo que suena y lo que sigue")
            self.render_queue(); return
        if n=="Recientes":
            self.cards.hide();self.rows=self.d.recent();self.section.setText("Continuar escuchando · reproducido recientemente")
            self.list.clear();self.render_rows(self.rows);self.render_stream_rows(self.d.recent_streams(),"Streaming reciente");return
        if n in ("Descargas","Offline"):
            self.cards.hide();self.rows=[];self.heading.setText("Offline");self.section.setText("Tu música descargada · disponible sin conexión");self.render_downloads();return
        else:
            self.section.setText("Descubre" if n=="Inicio" else "Tu colección")
        if n=="Estadísticas":self.show_stats()
        elif n=="Smart Playlists":
            self.list.clear();self.rows=[]
            for name in ("Más escuchadas","No escuchadas recientemente","Favoritas","Mejor valoradas"):
                it=QListWidgetItem("◉  "+name);it.setData(Qt.ItemDataRole.UserRole,("smart",name));self.list.addItem(it)
        else:
            self.refresh()
            if n in ("Inicio","Artistas","Álbumes","Top Picks"):
                QTimer.singleShot(120,lambda v=n:self.load_hybrid_view(v))

    def populate_cards(self):
        if not hasattr(self,"cards"):return
        self.cards.clear();theme=THEMES[self.theme];allrows=self.d.songs()
        if self.view=="Álbumes":
            groups=[("album",name) for name,_ in self.d.groups("album")][:14]
        elif self.view=="Artistas":
            groups=[("artist",name) for name,_ in self.d.groups("artist")][:14]
        elif self.view=="Géneros":
            groups=[("genre",name) for name,_ in self.d.groups("genre")][:14]
        else:
            # Recomendaciones: favoritos y mejor valoradas, evitando repetir álbum.
            ordered=sorted(allrows,key=lambda r:(r[7],r[8]),reverse=True)
            groups=[];seen=set()
            for r in ordered:
                name=r[3] or r[2]
                kind="album" if r[3] else "artist"
                if name and (kind,name) not in seen:
                    seen.add((kind,name));groups.append((kind,name))
                if len(groups)>=10:break
        indexes={"artist":2,"album":3,"genre":4}
        for kind,name in groups:
            rows=[r for r in allrows if r[indexes[kind]]==name]
            px=artwork_for_rows(rows,150)
            if px is None:
                # Never leave a blank square: generated gradient fallback.
                px=gradient_pixmap(150,theme["accent"],theme["accent2"])
            item=QListWidgetItem(QIcon(px),f"{name}\n{len(rows)} canciones")
            item.setData(Qt.ItemDataRole.UserRole,(kind,name))
            self.cards.addItem(item)

    def card_activate(self,it):
        d=it.data(Qt.ItemDataRole.UserRole)
        if not d:return
        if isinstance(d,tuple) and d and d[0]=="online_search":
            self.search.setText(d[1]); self.search_youtube(); return
        views={"album":"Álbumes","artist":"Artistas","genre":"Géneros"}
        self.filter=d; self.view=views.get(d[0],self.view); self.heading.setText(d[1]); self.cards.hide(); self.refresh()

    def _hybrid_seed(self,view):
        rows=self.d.songs()
        fav=[r for r in rows if r[7]]
        rated=sorted(rows,key=lambda r:(r[8],r[7]),reverse=True)
        src=fav[:20]+rated[:20]+rows[:20]
        artists=[]
        albums=[]
        for r in src:
            if r[2] and r[2]!="Artista desconocido" and r[2] not in artists:artists.append(r[2])
            if r[3] and r[3]!="Álbum desconocido" and r[3] not in albums:albums.append(r[3])
        if view=="Artistas":
            return ((artists[0]+" artistas similares música") if artists else "artistas música popular")
        if view=="Álbumes":
            return ((artists[0]+" álbum canciones") if artists else "álbumes música populares")
        if view=="Top Picks":
            return ((" ".join(artists[:2])+" mejores canciones") if artists else "música tendencias éxitos")
        return ((artists[0]+" mix canciones") if artists else "música recomendada tendencias")

    def load_hybrid_view(self,view):
        if self.view!=view or self.search.text().strip():return
        if self.hybrid_process and self.hybrid_process.state()!=QProcess.ProcessState.NotRunning:
            self.hybrid_process.kill()
        self.hybrid_token+=1;token=self.hybrid_token
        q=self._hybrid_seed(view)
        p=QProcess(self);self.hybrid_process=p;p.setProgram(YTDLP)
        p.setArguments(["--flat-playlist","--dump-single-json","--no-warnings","--no-playlist",f"ytsearch8:{q}"])
        p.finished.connect(lambda code,status,v=view,tok=token:self._hybrid_results(p,code,v,tok))
        p.start()

    def _hybrid_results(self,p,code,view,token):
        if p is not self.hybrid_process or token!=self.hybrid_token or self.view!=view or self.search.text().strip():return
        if code!=0:return
        try:entries=(json.loads(bytes(p.readAllStandardOutput()).decode("utf-8","ignore")).get("entries") or [])
        except Exception:return
        found=[]
        for e in entries:
            vid=str(e.get("id") or "")
            if not self._valid_video_id(vid):continue
            r={"id":vid,"title":str(e.get("title") or "Sin título"),"artist":str(e.get("channel") or e.get("uploader") or "YouTube"),
               "thumb":resolve_thumb(e,vid),"duration":int(e.get("duration") or 0)}
            self.d.upsert_stream(r);found.append(r)
        if not found:return
        if view=="Inicio":
            self._render_hybrid_tracks(found,"Recomendaciones para ti")
        elif view=="Top Picks":
            self._render_hybrid_tracks(found,"Tendencias para descubrir")
        elif view=="Artistas":
            self._render_hybrid_cards(found,"También podrían gustarte",artist_mode=True)
        elif view=="Álbumes":
            self._render_hybrid_cards(found,"Nuevos álbumes y canciones",artist_mode=False)

    def load_home_trending(self):
        if self.view!="Inicio" or self.search.text().strip():return
        p=QProcess(self);p.setProgram(YTDLP)
        p.setArguments(["--flat-playlist","--dump-single-json","--no-warnings","--no-playlist","ytsearch6:música tendencias éxitos"])
        def done(code,status):
            if code!=0 or self.view!="Inicio":return
            try:entries=(json.loads(bytes(p.readAllStandardOutput()).decode("utf-8","ignore")).get("entries") or [])
            except Exception:return
            rows=[]
            for e in entries:
                vid=str(e.get("id") or "")
                if not self._valid_video_id(vid):continue
                r={"id":vid,"title":str(e.get("title") or "Sin título"),"artist":str(e.get("channel") or e.get("uploader") or "YouTube"),
                   "thumb":resolve_thumb(e,vid),"duration":int(e.get("duration") or 0)}
                self.d.upsert_stream(r);rows.append((vid,r["title"],r["artist"],r["thumb"],r["duration"],0))
            if rows:self.render_stream_rows(rows,"Tendencias ahora")
        p.finished.connect(done);p.start()
        # Keep a reference so QProcess is not garbage-collected.
        self._home_trending_process=p

    def render_discover(self,rows,append=False):
        if not append:self.discoverList.clear()
        existing=set()
        for n in range(self.discoverList.count()):
            d=self.discoverList.item(n).data(Qt.ItemDataRole.UserRole)
            if isinstance(d,dict):existing.add(d.get("id"))
        for r in rows[:8]:
            if r.get("id") in existing:continue
            it=QListWidgetItem(f"▶  {r.get('title','')}\n    {r.get('artist','')}")
            it.setData(Qt.ItemDataRole.UserRole,dict(r));self.discoverList.addItem(it)

    def discover_activate(self,it):
        r=it.data(Qt.ItemDataRole.UserRole)
        if isinstance(r,dict):self.play_stream_dict(r)

    def _render_hybrid_tracks(self,rows,title):
        # Evita duplicados visuales con canciones locales del mismo título/artista.
        local={(r[1].strip().lower(),r[2].strip().lower()) for r in self.d.songs()}
        clean=[];seen=set()
        for r in rows:
            k=(r["title"].strip().lower(),r["artist"].strip().lower())
            if k in local or r["id"] in seen:continue
            seen.add(r["id"]);clean.append((r["id"],r["title"],r["artist"],r["thumb"],r["duration"],0))
        if clean:self.render_stream_rows(clean,title)

    def _render_hybrid_cards(self,rows,title,artist_mode=False):
        if not hasattr(self,"cards"):return
        seen=set()
        for r in rows:
            name=r["artist"] if artist_mode else r["title"]
            if not name or name in seen:continue
            seen.add(name)
            px=gradient_pixmap(150,THEMES[self.theme]["accent"],THEMES[self.theme]["accent2"])
            item=QListWidgetItem(QIcon(px),f"{name}\n{'YouTube · artista' if artist_mode else 'YouTube · descubrir'}")
            item.setData(Qt.ItemDataRole.UserRole,("online_search",name))
            self.cards.addItem(item)
            if len(seen)>=6:break
        if seen:
            self.section.setText("Tu colección + "+title)

    def show_stats(self):
        self.list.clear();self.rows=[];total,top=self.d.stats();plays,seconds=total
        h=QListWidgetItem(f"Tu biblioteca en números\n{plays} reproducciones • {seconds//3600} horas registradas");h.setFlags(Qt.ItemFlag.NoItemFlags);self.list.addItem(h)
        mp={r[0]:r for r in self.d.songs()}
        for i,(p,n) in enumerate(top,1):
            if p in mp:
                r=mp[p];it=QListWidgetItem(f"{i:02d}  {r[1]}\n     {r[2]} • {n} reproducciones");it.setData(Qt.ItemDataRole.UserRole,p);self.list.addItem(it)

    def _add_search_header(self,text):
        it=QListWidgetItem(text);it.setFlags(Qt.ItemFlag.NoItemFlags);f=it.font();f.setBold(True);it.setFont(f);self.list.addItem(it)
    def _remove_search_headers(self):
        for i in range(self.list.count()-1,-1,-1):
            it=self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole)=="search-header":self.list.takeItem(i)
    def _render_unified_local_header(self):
        q=self.search.text().strip()
        if len(q)<2:return
        # refresh() already rendered local rows; add a clear source heading at the top.
        it=QListWidgetItem("En tu biblioteca");it.setFlags(Qt.ItemFlag.NoItemFlags);it.setData(Qt.ItemDataRole.UserRole,"search-header")
        f=it.font();f.setBold(True);it.setFont(f);self.list.insertItem(0,it)
    def _add_search_header(self,text):
        it=QListWidgetItem(text);it.setFlags(Qt.ItemFlag.NoItemFlags);it.setData(Qt.ItemDataRole.UserRole,"search-header");f=it.font();f.setBold(True);it.setFont(f);self.list.addItem(it)

    def search_changed(self):
        self.refresh()
        q=self.search.text().strip()
        if len(q)>=2:
            self.stream_search_timer.start();self.deezer_timer.start()
        else:
            self.stream_search_timer.stop();self.deezer_timer.stop()


    def _valid_video_id(self,vid):
        return bool(YT_ID_RE.fullmatch(str(vid or "")))

    def search_deezer(self):
        q=self.search.text().strip()[:100]
        if len(q)<2:return
        # Deezer is a metadata/catalog enrichment provider here; playback remains
        # local or YouTube. No private app code, tokens, DRM or media URLs are used.
        from urllib.parse import quote
        url=QUrl("https://api.deezer.com/search?q="+quote(q))
        rep=self.net.get(QNetworkRequest(url))
        rep.finished.connect(lambda:self._deezer_results(rep,q))

    def _deezer_results(self,rep,q):
        if q!=self.search.text().strip()[:100]:rep.deleteLater();return
        try:data=json.loads(bytes(rep.readAll()).decode("utf-8","ignore"))
        except Exception:data={}
        rep.deleteLater()
        rows=(data.get("data") or [])[:6]
        if not rows:return
        self._add_search_header("Deezer · catálogo")
        for x in rows:
            title=str(x.get("title") or "");artist=str((x.get("artist") or {}).get("name") or "")
            album=str((x.get("album") or {}).get("title") or "");cover=str((x.get("album") or {}).get("cover_medium") or "")
            it=QListWidgetItem(f"◇  {title}\n    {artist}  •  {album}")
            it.setToolTip("Metadatos de Deezer · doble clic para buscar esta canción en las fuentes reproducibles")
            it.setData(Qt.ItemDataRole.UserRole,("deezer_search",(title+" "+artist).strip(),cover))
            self.list.addItem(it)

    def search_youtube(self):
        q=self.search.text().strip()[:120]
        self.stream_rows=[]
        if self.view=="Streaming":self.list.clear()
        if len(q)<2:
            it=QListWidgetItem("Escribe al menos 2 caracteres para buscar en YouTube"); it.setFlags(Qt.ItemFlag.NoItemFlags); self.list.addItem(it); return
        if self.stream_process and self.stream_process.state()!=QProcess.ProcessState.NotRunning:
            self.stream_process.kill()
        if self.view=="Streaming":
            self.heading.setText("Buscando en YouTube…")
            it=QListWidgetItem("Buscando canciones…"); it.setFlags(Qt.ItemFlag.NoItemFlags); self.list.addItem(it)
        else:
            self._add_search_header("YouTube · buscando…")
        p=QProcess(self); self.stream_process=p
        p.setProgram(YTDLP)
        p.setArguments(["--flat-playlist","--dump-single-json","--no-warnings","--no-playlist",f"ytsearch12:{q}"])
        p.finished.connect(lambda code,status:self._youtube_results(p,code))
        p.start()

    def _youtube_results(self,p,code):
        if p is not self.stream_process:return
        if self.view=="Streaming":
            self.list.clear(); self.heading.setText("Resultados de YouTube")
        else:
            # Conserva la sección local y añade la fuente online debajo.
            for i in range(self.list.count()-1,-1,-1):
                it=self.list.item(i)
                if it.data(Qt.ItemDataRole.UserRole)=="youtube-header":self.list.takeItem(i)
            it=QListWidgetItem("YouTube");it.setFlags(Qt.ItemFlag.NoItemFlags);it.setData(Qt.ItemDataRole.UserRole,"youtube-header");f=it.font();f.setBold(True);it.setFont(f);self.list.addItem(it)
        if code!=0:
            msg=bytes(p.readAllStandardError()).decode("utf-8","ignore").strip()
            it=QListWidgetItem("No se pudo consultar YouTube. Comprueba tu conexión e inténtalo otra vez."+("\n"+msg[:180] if msg else ""))
            it.setFlags(Qt.ItemFlag.NoItemFlags); self.list.addItem(it); return
        try:
            data=json.loads(bytes(p.readAllStandardOutput()).decode("utf-8","ignore"))
            entries=data.get("entries") or []
        except Exception:
            entries=[]
        for e in entries:
            vid=str(e.get("id") or "")
            if not self._valid_video_id(vid):continue
            title=str(e.get("title") or "Sin título"); artist=str(e.get("channel") or e.get("uploader") or "YouTube")
            dur=int(e.get("duration") or 0); row={"id":vid,"title":title,"artist":artist,"duration":dur,"thumb":resolve_thumb(e,vid)}
            self.d.upsert_stream(row)
            self.stream_rows.append(row)
            mm,ss=divmod(dur,60)
            it=QListWidgetItem(f"▶  {title}\n     {artist}  •  {mm}:{ss:02d}" if dur else f"▶  {title}\n     {artist}")
            it.setData(Qt.ItemDataRole.UserRole,("stream",len(self.stream_rows)-1)); self.list.addItem(it)
        if not self.stream_rows:
            it=QListWidgetItem("No encontré resultados reproducibles");it.setFlags(Qt.ItemFlag.NoItemFlags);self.list.addItem(it)

    def play_stream(self,index):
        if not (0<=index<len(self.stream_rows)):return
        self.play_stream_dict(self.stream_rows[index])

    def play_stream_dict(self,row):
        vid=row["id"]
        if not self._valid_video_id(vid):return
        if self.resolve_process and self.resolve_process.state()!=QProcess.ProcessState.NotRunning:self.resolve_process.kill()
        self.local_current=None; self.stream_current=row; self.now.setText(f'{row["title"]}\n{row["artist"]} • YouTube')
        self.statusBar().showMessage("Preparando streaming…",3000)
        p=QProcess(self); self.resolve_process=p; p.setProgram(YTDLP)
        p.setArguments(["-g","-f","bestaudio[ext=m4a]/bestaudio/best","--no-playlist","--no-warnings",f"https://www.youtube.com/watch?v={vid}"])
        p.finished.connect(lambda code,status:self._stream_resolved(p,code,row))
        p.start()

    def _stream_resolved(self,p,code,row):
        if p is not self.resolve_process:return
        if code!=0:
            self.statusBar().showMessage("YouTube no respondió. La biblioteca y otros proveedores siguen disponibles.",5000);return
        url=bytes(p.readAllStandardOutput()).decode("utf-8","ignore").strip().splitlines()
        url=url[0].strip() if url else ""
        if not url.startswith("https://"):
            self.statusBar().showMessage("Fuente de streaming no válida",5000);return
        self.fadeTimer.stop();self.crossfade_started=False;self.au.setVolume(self.base_volume)
        self.p.setSource(QUrl(url));self.p.play();self.d.add_stream_history(row["id"]);self.update_playing_indicator()
        self.now.setText(f'{row["title"]}\n{row["artist"]} • YouTube')
        self.cover.setPixmap(QPixmap());self.cover.setText("◉")
        self._load_stream_thumb(row)
        self.statusBar().showMessage("Reproduciendo desde YouTube",2500)

    def _cover_request(self,url):
        """Crea una petición de carátula tolerante a CDN/redirects."""
        req=QNetworkRequest(QUrl(url))
        req.setRawHeader(b"User-Agent",b"Mozilla/5.0 Sakukelly/5.11.0")
        req.setRawHeader(b"Accept",b"image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8")
        try:req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        except Exception:pass
        return req

    def _set_stream_cover(self,px,vid,cache=None):
        """Muestra la portada solo si sigue sonando el mismo vídeo."""
        if px is None or px.isNull():return False
        if cache:
            try:px.save(str(cache),"PNG")
            except Exception:pass
        if self.stream_current and str(self.stream_current.get("id") or "")==str(vid):
            self.cover.setText("")
            self.cover.setPixmap(px.scaled(300,300,Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation))
        return True

    def _load_stream_thumb(self,row):
        # 5.10.3: prioridad solicitada: YouTube primero; Deezer solo como respaldo.
        # Usamos una clave nueva para no reutilizar portadas antiguas de Deezer
        # guardadas por versiones 5.10.1/5.10.2 bajo el mismo id de YouTube.
        vid=str(row.get("id") or "").strip()
        key=hashlib.sha1(("ytfirst-v2:"+vid).encode("utf-8","ignore")).hexdigest()
        cache=COVER_CACHE/(key+".png")
        if cache.exists():
            px=QPixmap(str(cache))
            if self._set_stream_cover(px,vid):return
        self._load_youtube_cover(row,cache)

    def _load_youtube_cover(self,row,cache):
        """Intenta varias miniaturas oficiales de YouTube antes de usar Deezer."""
        vid=str(row.get("id") or "").strip()
        urls=[]
        thumb=str(row.get("thumb") or "").strip()
        if thumb.startswith("https://"):urls.append(thumb)
        if self._valid_video_id(vid):
            # maxres puede no existir; hqdefault es el respaldo más compatible.
            urls.extend([
                f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg",
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            ])
        # elimina duplicados conservando el orden
        urls=list(dict.fromkeys(u for u in urls if u.startswith("https://")))
        if not urls:return self._load_stream_deezer_cover(row,cache)

        def attempt(pos):
            if pos>=len(urls):return self._load_stream_deezer_cover(row,cache)
            rep=self.net.get(self._cover_request(urls[pos]))
            def done():
                ok=False
                try:
                    if rep.error()==QNetworkReply.NetworkError.NoError:
                        raw=bytes(rep.readAll());px=QPixmap()
                        if px.loadFromData(raw):ok=self._set_stream_cover(px,vid,cache)
                except Exception:pass
                finally:rep.deleteLater()
                if not ok:attempt(pos+1)
            rep.finished.connect(done)
        attempt(0)

    def _load_stream_deezer_cover(self,row,cache):
        """Respaldo: busca una portada de álbum fiable en Deezer."""
        from urllib.parse import quote
        from difflib import SequenceMatcher
        vid=str(row.get("id") or "");title=str(row.get("title") or "").strip();artist=str(row.get("artist") or "").strip()
        def norm(s):
            s=str(s or "").lower()
            s=re.sub(r"(?i)\b(official|music|video|audio|lyrics?|visualizer|hd|4k|remaster(?:ed)?|live)\b"," ",s)
            s=re.sub(r"[^a-z0-9áéíóúüñ ]+"," ",s)
            return " ".join(s.split())
        clean_title=norm(title);clean_artist=norm(artist)
        q=(artist+" "+clean_title).strip()[:150]
        if not q:return
        rep=self.net.get(self._cover_request("https://api.deezer.com/search?q="+quote(q)))
        def meta_done():
            url="";best_score=0.0
            try:
                data=json.loads(bytes(rep.readAll()).decode("utf-8","ignore"));items=(data.get("data") or [])[:12]
                target_duration=int(row.get("duration") or 0)
                for item in items:
                    dz_title=norm(item.get("title_short") or item.get("title"))
                    dz_artist=norm((item.get("artist") or {}).get("name"))
                    title_score=SequenceMatcher(None,clean_title,dz_title).ratio() if clean_title and dz_title else 0
                    artist_score=SequenceMatcher(None,clean_artist,dz_artist).ratio() if clean_artist and dz_artist else (0.72 if not clean_artist else 0)
                    dur=int(item.get("duration") or 0)
                    duration_score=1.0 if not target_duration or not dur else max(0.0,1.0-abs(target_duration-dur)/max(18.0,target_duration*0.18))
                    score=(title_score*0.58)+(artist_score*0.30)+(duration_score*0.12)
                    if score>best_score:
                        album=item.get("album") or {};candidate=str(album.get("cover_xl") or album.get("cover_big") or album.get("cover_medium") or "")
                        if candidate.startswith("https://"):best_score=score;url=candidate
                if best_score<0.72:url=""
            except Exception:pass
            rep.deleteLater()
            if not url:return
            img=self.net.get(self._cover_request(url))
            def image_done():
                try:
                    if img.error()==QNetworkReply.NetworkError.NoError:
                        raw=bytes(img.readAll());px=QPixmap()
                        if px.loadFromData(raw):self._set_stream_cover(px,vid,cache)
                except Exception:pass
                finally:img.deleteLater()
            img.finished.connect(image_done)
        rep.finished.connect(meta_done)

    def local_video_query(self):
        r=self.local_current
        if not r:return ""
        artist=(r.get("artist") or "").strip();title=(r.get("title") or "").strip()
        return (artist+" "+title+" official video").strip()[:160]

    def find_local_video(self,force=False,callback=None):
        r=self.local_current
        if not r:
            if callback:callback(None)
            return
        cached=self.d.local_video(r["path"])
        if cached and not force:
            if callback:callback(cached)
            return
        q=self.local_video_query()
        if not q:
            if callback:callback(None)
            return
        if getattr(self,"local_video_search",None) and self.local_video_search.state()!=QProcess.ProcessState.NotRunning:self.local_video_search.kill()
        p=QProcess(self);self.local_video_search=p;p.setProgram(YTDLP)
        p.setArguments(["--flat-playlist","--dump-single-json","--no-warnings","--no-playlist",f"ytsearch5:{q}"])
        def done(code,status):
            if p is not self.local_video_search or code!=0:
                if callback:callback(None)
                return
            try:entries=(json.loads(bytes(p.readAllStandardOutput()).decode("utf-8","ignore")).get("entries") or [])
            except Exception:entries=[]
            best=None
            for e in entries:
                vid=str(e.get("id") or "")
                if not self._valid_video_id(vid):continue
                best={"id":vid,"title":str(e.get("title") or ""),"artist":str(e.get("channel") or e.get("uploader") or "YouTube"),"thumb":resolve_thumb(e,vid),"offset_ms":0};break
            if best:self.d.set_local_video(r["path"],best,0)
            if callback:callback(best)
        p.finished.connect(done);p.start();self.statusBar().showMessage("Buscando videoclip para tu canción local…",2500)

    def choose_local_video(self):
        if not self.local_current:return
        q,ok=QInputDialog.getText(self,"Elegir vídeo de YouTube","Buscar vídeo:",text=self.local_video_query())
        if not ok or not q.strip():return
        # Temporarily search with the user query and store the first result.
        old_title=self.local_current["title"];old_artist=self.local_current["artist"]
        self.local_current["title"]=q.strip();self.local_current["artist"]=""
        def done(r):
            self.local_current["title"]=old_title;self.local_current["artist"]=old_artist
            if r:self.statusBar().showMessage("Vídeo asociado a la canción",2500)
        self.find_local_video(True,done)

    def open_video_player(self):
        if getattr(self,"video_player",None) is None:
            self.video_player=VideoPlayer(self)
        self.video_player.show();self.video_player.raise_();self.video_player.activateWindow()
        self.video_player.load_current()

    def download_current_stream(self):
        row=self.stream_current
        if not row:
            QMessageBox.information(self,"Descargas","Reproduce primero una canción de la sección Streaming.");return
        if not self._valid_video_id(row.get("id")):return
        answer=QMessageBox.question(self,"Descargar","Descarga únicamente contenido que tengas derecho o autorización para guardar.\n\n¿Continuar?")
        if answer!=QMessageBox.StandardButton.Yes:return
        folder=QFileDialog.getExistingDirectory(self,"Guardar descarga",str(Path.home()/"Music"))
        if not folder:return
        if self.download_process and self.download_process.state()!=QProcess.ProcessState.NotRunning:
            QMessageBox.information(self,"Descargas","Ya hay una descarga en curso.");return
        p=QProcess(self);self.download_process=p;p.setProgram(YTDLP)
        template=str(Path(folder)/"%(title).150s [%(id)s].%(ext)s")
        p.setArguments(["--no-playlist","--no-warnings","--ffmpeg-location",FFMPEG,"-x","--audio-format","mp3","--audio-quality","0","--embed-metadata","--embed-thumbnail","--print","after_move:filepath","-o",template,f'https://www.youtube.com/watch?v={row["id"]}'])
        p.readyReadStandardOutput.connect(lambda:self.statusBar().showMessage(bytes(p.readAllStandardOutput()).decode("utf-8","ignore").strip()[-180:],2500))
        p.finished.connect(lambda code,status,r=dict(row),proc=p:self._download_done(code,folder,r,proc))
        p.start();self.statusBar().showMessage("Descarga iniciada…",3000)

    def _download_done(self,code,folder,row=None,proc=None):
        if code==0:
            output=bytes(proc.readAllStandardOutput()).decode("utf-8","ignore").strip().splitlines() if proc else []
            path=next((x.strip() for x in reversed(output) if Path(x.strip()).exists()),"")
            if row and path:self.d.add_download(row.get("id",""),row.get("title",""),row.get("artist",""),path)
            self.statusBar().showMessage(f"Descarga terminada en {folder}",7000)
            if self.view in ("Descargas","Offline"):self.render_downloads()
        else:self.statusBar().showMessage("La descarga no pudo completarse",7000)

    def refresh(self):
        q=self.search.text().strip(); self.list.clear(); self.stream_rows=[]
        if not q and self.view=="Inicio":
            self.section.setText("Descubre")
        elif not q and self.view=="Artistas":
            self.section.setText("Tus artistas y nuevos descubrimientos")
        elif not q and self.view=="Álbumes":
            self.section.setText("Álbumes de tu colección y para descubrir")
        elif not q and self.view=="Top Picks":
            self.section.setText("Selección para ti")
        if q:
            self.rows=self.d.songs(q,False); self.heading.setText("Búsqueda unificada"); self.cards.hide()
            self._add_search_header("En tu biblioteca")
            self.render_rows(self.rows)
            if not self.rows:
                it=QListWidgetItem("No encontré coincidencias locales"); it.setFlags(Qt.ItemFlag.NoItemFlags); self.list.addItem(it)
            return
        if self.view=="Carpetas": self.render_folders(); return
        if self.view=="Playlists" and not self.filter:
            self.rows=[]
            for n in self.d.pls():
                it=QListWidgetItem("▣  "+n); it.setData(Qt.ItemDataRole.UserRole,("playlist",n)); self.list.addItem(it)
            return
        if self.view=="Playlists" and self.filter: self.rows=self.d.plsongs(self.filter[1])
        else: self.rows=self.d.songs("",self.view=="Favoritos")
        if self.view=="Top Picks": self.rows=sorted(self.rows,key=lambda r:(r[8],r[7]),reverse=True)[:50]
        if self.filter and self.view!="Playlists":
            idx={"artist":2,"album":3,"genre":4}.get(self.filter[0])
            if idx is not None:self.rows=[r for r in self.rows if r[idx]==self.filter[1]]
        if self.view=="Géneros" and not self.filter:
            self.rows=[]
            for name,count in self.d.groups("genre"):
                it=QListWidgetItem(f"≡  {name}\n    {count} canciones"); it.setData(Qt.ItemDataRole.UserRole,("group","genre",name)); self.list.addItem(it)
            return
        if self.view=="Inicio":
            recent=self.d.recent(10)
            if recent:
                self.render_rows(recent)
            elif self.rows:
                self.render_rows(self.rows[:10])
            return
        elif self.view=="Top Picks" and self.rows:self._add_search_header("En tu biblioteca")
        self.render_rows(self.rows)
        if self.view=="Favoritos":
            sf=self.d.stream_favs()
            if sf:self.render_stream_rows(sf,"Favoritos de YouTube")
        elif self.view=="Playlists" and self.filter:
            sp=self.d.stream_plsongs(self.filter[1])
            if sp:self.render_stream_rows(sp,"Streaming")

    def render_rows(self,rows):
        for no,x in enumerate(rows,1):
            p,t,a,al,g,y,d,f,rate=x; mm,ss=divmod(int(d),60); stars="★"*rate+"☆"*(5-rate)
            it=QListWidgetItem(f"{no:02d}    {'♥' if f else '♡'}   {t}\n        {a}  •  {al}     {mm}:{ss:02d}     {stars}")
            it.setData(Qt.ItemDataRole.UserRole,p)
            if self.local_current and self.local_current.get("path")==p:
                it.setText("▮▮  REPRODUCIENDO   "+it.text())
                f=it.font();f.setBold(True);it.setFont(f);it.setBackground(self._now_playing_brush())
            self.list.addItem(it)

    def render_stream_rows(self,rows,title=None):
        if title:self._add_search_header(title)
        base=len(self.stream_rows)
        for x in rows:
            r={"id":x[0],"title":x[1],"artist":x[2],"thumb":x[3],"duration":x[4],"fav":x[5]}
            self.stream_rows.append(r);mm,ss=divmod(int(r["duration"] or 0),60)
            it=QListWidgetItem(f"◉    {'♥' if r.get('fav') else '♡'}   {r['title']}\n        {r['artist']}  •  YouTube     {mm}:{ss:02d}")
            it.setData(Qt.ItemDataRole.UserRole,("stream",len(self.stream_rows)-1))
            if self.stream_current and self.stream_current.get("id")==r.get("id"):
                it.setText("▮▮  REPRODUCIENDO   "+it.text())
                f=it.font();f.setBold(True);it.setFont(f);it.setBackground(self._now_playing_brush())
            self.list.addItem(it)

    def render_folders(self):
        self.list.clear(); self.rows=[]; roots=[x[0] for x in self.d.c.execute("SELECT path FROM folders ORDER BY path")]
        if self.currentFolder is None:
            self.heading.setText("Tus carpetas")
            if not roots:
                it=QListWidgetItem("＋ Añade una carpeta de música para empezar"); it.setFlags(Qt.ItemFlag.NoItemFlags); self.list.addItem(it); return
            for f in roots:
                count=sum(1 for r in self.d.songs() if str(r[0]).startswith(str(Path(f))+"/"))
                it=QListWidgetItem(f"▤  {Path(f).name or f}\n    {f}  •  {count} canciones"); it.setData(Qt.ItemDataRole.UserRole,("folder",f)); self.list.addItem(it)
            return
        folder=Path(self.currentFolder); self.heading.setText(folder.name or str(folder))
        up=QListWidgetItem("←  Volver"); up.setData(Qt.ItemDataRole.UserRole,("folder_up",str(folder.parent))); self.list.addItem(up)
        for f in child_folders(self.d,str(folder)):
            it=QListWidgetItem("▤  "+Path(f).name); it.setData(Qt.ItemDataRole.UserRole,("folder",f)); self.list.addItem(it)
        self.rows=folder_rows(self.d,str(folder)); self.render_rows(self.rows)

    def player_drop(self,payload):
        """Play immediately when a library item or audio file is dropped on the player."""
        kind,data=payload
        if kind=="files":
            path=next((x for x in data if Path(x).suffix.lower() in EXT and Path(x).exists()),None)
            if not path:return
            row=next((r for r in self.d.songs() if r[0]==path),None)
            if row:
                self.rows=[row];self.i=0;self.playrow(0)
            else:
                title=Path(path).stem;artist="";album="Archivo"
                try:
                    a=MF(path,easy=True) if MF else None
                    if a:
                        title=(a.get("title") or [title])[0];artist=(a.get("artist") or [""])[0];album=(a.get("album") or ["Archivo"])[0]
                except Exception:pass
                self.stream_current=None;self.local_current={"path":path,"title":title,"artist":artist,"album":album}
                self.p.setSource(QUrl.fromLocalFile(path));self.p.play();self.now.setText(f"{title}\\n{artist or album}")
                px=embedded_or_folder_cover(path,300);self.cover.setPixmap(px if px else QPixmap());self.cover.setText("" if px else "♪")
            self.statusBar().showMessage("Reproduciendo archivo arrastrado",2200);return
        d=data
        if isinstance(d,list):d=tuple(d)
        if isinstance(d,tuple) and d:
            if d[0]=="stream":self.play_stream(int(d[1]));return
            if d[0]=="download":
                path=d[2]
                if Path(path).exists():
                    self.stream_current=None;self.local_current={"path":path,"title":Path(path).stem,"artist":"","album":"Offline"};self.p.setSource(QUrl.fromLocalFile(path));self.p.play();self.now.setText(Path(path).stem+"\\nOffline")
                return
        if isinstance(d,str):
            idx=next((j for j,r in enumerate(self.rows) if r[0]==d),-1)
            if idx>=0:self.i=idx;self.playrow(idx)

    def activate(self):
        it=self.list.currentItem()
        if not it:return
        d=it.data(Qt.ItemDataRole.UserRole)
        if isinstance(d,tuple) and d and d[0]=="download":
            path=d[2]
            if Path(path).exists():
                self.stream_current=None;self.local_current={"path":path,"title":self.list.currentItem().text().split("\n")[0].replace("⇩  ",""),"artist":"","album":"Descargas"}
                self.p.setSource(QUrl.fromLocalFile(path));self.p.play();self.now.setText(self.local_current["title"]+"\nDisponible sin conexión")
            return
        if isinstance(d,tuple) and d and d[0]=="deezer_search":
            self.search.setText(d[1]);self.search_youtube();return
        if isinstance(d,tuple) and d and d[0]=="stream":
            self.play_stream(d[1]); return
        if isinstance(d,tuple) and d and d[0]=="queue_item":
            idx=d[1]
            if 0<=idx<len(self.queue):
                entry=self.queue[idx]; del self.queue[:idx+1]
                if isinstance(entry,tuple) and entry[0]=="stream":
                    r=self.d.stream(entry[1])
                    if r:self.play_stream_dict(r)
                else:
                    path=entry[1] if isinstance(entry,tuple) else entry
                    r=next((x for x in self.d.songs() if x[0]==path),None)
                    if r:self.rows=[r];self.i=0;self.playrow(0)
            if self.view=="Cola":self.render_queue()
            return
        if isinstance(d,tuple):
            if d[0]=="smart":
                self.rows=self.d.smart(d[1]); self.heading.setText(d[1]); self.list.clear(); self.render_rows(self.rows); return
            if d[0]=="folder": self.currentFolder=d[1]; self.render_folders(); return
            if d[0]=="folder_up":
                roots=[Path(x[0]) for x in self.d.c.execute("SELECT path FROM folders")]; parent=Path(d[1])
                self.currentFolder=None if any(parent==r.parent or parent==r for r in roots) else str(parent); self.render_folders(); return
            if d[0]=="playlist": self.filter=("playlist",d[1]); self.heading.setText(d[1]); self.refresh(); return
            if d[0]=="group": self.filter=(d[1],d[2]); self.heading.setText(d[2]); self.refresh(); return
        idx=next((j for j,r in enumerate(self.rows) if r[0]==d),-1)
        if idx>=0:self.i=idx; self.playrow(idx)

    def playrow(self,i):
        if 0<=i<len(self.rows):
            self.fadeTimer.stop(); self.crossfade_started=False; self._pending_next=None; self.au.setVolume(self.base_volume); self.p2.stop(); self.au2.setVolume(0.0)
            x=self.rows[i]; self.stream_current=None; self.local_current={"path":x[0],"title":x[1],"artist":x[2],"album":x[3]}; self.p.setSource(QUrl.fromLocalFile(x[0])); self.p.play(); self.update_playing_indicator(); self.d.played(x[0]); self.now.setText(f"{x[1]}\n{x[2]} • {x[3]}"); self.art(x[0])
        if self.video_player:
            self.video_player.load_current()

    def _now_playing_brush(self):
        c=QColor(THEMES[self.theme]["accent"]);c.setAlpha(70);return c

    def update_playing_indicator(self):
        for n in range(self.list.count()):
            it=self.list.item(n);d=it.data(Qt.ItemDataRole.UserRole)
            txt=it.text().replace("▮▮  REPRODUCIENDO   ","")
            active=False
            if isinstance(d,str) and self.local_current:active=(d==self.local_current.get("path"))
            elif isinstance(d,tuple) and d and d[0]=="stream" and 0<=d[1]<len(self.stream_rows) and self.stream_current:
                active=(self.stream_rows[d[1]].get("id")==self.stream_current.get("id"))
            it.setText(("▮▮  REPRODUCIENDO   " if active else "")+txt)
            f=it.font();f.setBold(active);it.setFont(f)
            it.setBackground(self._now_playing_brush() if active else QColor(0,0,0,0))
        if self.view=="Cola":self.render_queue()

    def queue_reordered(self,*_):
        if self.view!="Cola":return
        ordered=[]
        for n in range(self.list.count()):
            d=self.list.item(n).data(Qt.ItemDataRole.UserRole)
            if isinstance(d,tuple) and d and d[0]=="queue_item":
                old=d[1]
                if 0<=old<len(self.queue):ordered.append(self.queue[old])
        if len(ordered)==len(self.queue):
            self.queue=ordered
            QTimer.singleShot(0,self.render_queue)

    def render_downloads(self):
        self.list.clear();rows=self.d.downloads()
        if not rows:
            it=QListWidgetItem("No hay descargas disponibles sin conexión.");it.setFlags(Qt.ItemFlag.NoItemFlags);self.list.addItem(it);return
        for i,title,artist,path,ts in rows:
            it=QListWidgetItem(f"⇩  {title}\n    {artist}  •  Disponible sin conexión")
            it.setData(Qt.ItemDataRole.UserRole,("download",i,path));self.list.addItem(it)

    def render_queue(self):
        self.list.clear()
        if self.local_current or self.stream_current:
            title=(self.local_current or self.stream_current)["title"]; artist=(self.local_current or self.stream_current)["artist"]
            source="Biblioteca local" if self.local_current else "YouTube"
            it=QListWidgetItem(f"▮▮  REPRODUCIENDO AHORA   {title}\n        {artist}  •  {source}")
            it.setFlags(Qt.ItemFlag.NoItemFlags); f=it.font(); f.setBold(True); it.setFont(f)
            it.setBackground(self._now_playing_brush()); self.list.addItem(it)
        if not self.queue:
            it=QListWidgetItem("La cola está vacía.\nAñade canciones con clic derecho > Añadir a la cola.")
            it.setFlags(Qt.ItemFlag.NoItemFlags); self.list.addItem(it)
            return
        header=QListWidgetItem("A CONTINUACIÓN"); header.setFlags(Qt.ItemFlag.NoItemFlags)
        f=header.font(); f.setBold(True); header.setFont(f); self.list.addItem(header)
        for idx,entry in enumerate(self.queue):
            kind=entry[0] if isinstance(entry,tuple) else "local"
            payload=entry[1] if isinstance(entry,tuple) else entry
            if kind=="stream":
                row=self.d.stream(payload); title=row["title"] if row else str(payload); artist=row["artist"] if row else "YouTube"; icon="◉"
            else:
                r=next((x for x in self.d.songs() if x[0]==payload),None)
                title=r[1] if r else Path(str(payload)).name; artist=r[2] if r else ""; icon="♫"
            it=QListWidgetItem(f"{idx+1:02d}   {icon}  {title}\n        {artist}")
            it.setData(Qt.ItemDataRole.UserRole,("queue_item",idx))
            self.list.addItem(it)

    def art(self,p):
        self.cover.setPixmap(QPixmap());self.cover.setText("♪")
        px=embedded_or_folder_cover(p,288)
        if px is not None:
            self.cover.setText("");self.cover.setPixmap(px.scaled(300,300,Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation))
        elif self.local_current:
            self.fetch_deezer_local_cover(self.local_current)

    def fetch_deezer_local_cover(self,row):
        path=str(row.get("path") or "");title=str(row.get("title") or "").strip();artist=str(row.get("artist") or "").strip()
        if not path or not title:return
        key=hashlib.sha1(path.encode("utf-8","ignore")).hexdigest();cache=COVER_CACHE/(key+".png")
        if cache.exists():
            px=QPixmap(str(cache))
            if not px.isNull():
                self.cover.setText("");self.cover.setPixmap(px.scaled(300,300,Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation))
            return
        token=(path,title,artist)
        if getattr(self,"_deezer_cover_pending",None)==token:return
        self._deezer_cover_pending=token
        from urllib.parse import quote
        q=(artist+" "+title).strip()[:140]
        rep=self.net.get(QNetworkRequest(QUrl("https://api.deezer.com/search?q="+quote(q))))
        def meta_done():
            try:
                data=json.loads(bytes(rep.readAll()).decode("utf-8","ignore"));items=data.get("data") or []
                best=items[0] if items else {};album=best.get("album") or {}
                url=str(album.get("cover_xl") or album.get("cover_big") or album.get("cover_medium") or "")
            except Exception:url=""
            rep.deleteLater()
            if not url.startswith("https://"):return
            img=self.net.get(QNetworkRequest(QUrl(url)))
            def image_done():
                try:
                    raw=bytes(img.readAll());px=QPixmap()
                    if px.loadFromData(raw):
                        px.save(str(cache),"PNG")
                        if self.local_current and self.local_current.get("path")==path:
                            self.cover.setText("");self.cover.setPixmap(px.scaled(300,300,Qt.AspectRatioMode.KeepAspectRatioByExpanding,Qt.TransformationMode.SmoothTransformation))
                finally:img.deleteLater()
            img.finished.connect(image_done)
        rep.finished.connect(meta_done)

    def open_current_on_youtube(self):
        from urllib.parse import quote_plus
        if self.stream_current:
            vid=str(self.stream_current.get("id") or "")
            if self._valid_video_id(vid):
                QDesktopServices.openUrl(QUrl("https://www.youtube.com/watch?v="+vid));return
        if self.local_current:
            title=str(self.local_current.get("title") or "");artist=str(self.local_current.get("artist") or "")
            q=(artist+" "+title+" official music video").strip()
            if q:QDesktopServices.openUrl(QUrl("https://www.youtube.com/results?search_query="+quote_plus(q)));return
        self.statusBar().showMessage("No hay una canción seleccionada para abrir en YouTube.",3000)

    def toggle(self):
        if self.p.playbackState()==QMediaPlayer.PlaybackState.PlayingState:self.p.pause()
        elif self.p.source().isEmpty():
            if self.rows:self.i=max(0,self.i); self.playrow(self.i)
        else:self.p.play()

    def set_volume(self,x):
        self.base_volume=x/100; self.au.setVolume(self.base_volume)

    def _peek_next_local(self):
        """Determina qué sonará después sin avanzar el estado, para poder precargarlo
        y solapar su audio con el de la canción actual (crossfade real)."""
        if self.queue:
            entry=self.queue[0]
            if isinstance(entry,tuple) and entry[0]=="stream":return None
            path=entry[1] if isinstance(entry,tuple) else entry
            return ("queue_path",path)
        if not self.rows:return None
        if self.repeat and not self.shuffle:return ("index",self.i)
        ni=random.randrange(len(self.rows)) if self.shuffle else (self.i+1)%len(self.rows)
        return ("index",ni)

    def _begin_crossfade(self):
        if self.crossfade_started or not self.rows: return
        self._pending_next=self._peek_next_local()
        if self._pending_next:
            kind,payload=self._pending_next
            path=payload if kind=="queue_path" else (self.rows[payload][0] if 0<=payload<len(self.rows) else None)
            if path:
                self.au2.setVolume(0.0); self.p2.setSource(QUrl.fromLocalFile(path)); self.p2.play()
            else:
                self._pending_next=None
        self.crossfade_started=True; self.fadeStart=int(time.time()*1000); self.fadeTimer.start()

    def _fade_step(self):
        elapsed=int(time.time()*1000)-self.fadeStart; dur=max(500,self.crossfade_ms)
        ratio=min(1.0,elapsed/dur)
        self.au.setVolume(self.base_volume*(1.0-ratio))
        if self._pending_next:self.au2.setVolume(self.base_volume*ratio)
        if ratio>=1.0:
            self.fadeTimer.stop(); self.crossfade_started=False; self.au.setVolume(self.base_volume)
            if self._pending_next:self._complete_crossfade()
            else:self.next()

    def _complete_crossfade(self):
        kind,payload=self._pending_next; self._pending_next=None
        carry_pos=self.p2.position()
        self.p2.stop(); self.au2.setVolume(0.0)
        if kind=="queue_path":
            path=payload
            if self.queue and (self.queue[0][1] if isinstance(self.queue[0],tuple) else self.queue[0])==path:self.queue.pop(0)
            r=next((x for x in self.d.songs() if x[0]==path),None)
            if not r:self.next();return
            self.rows=[r]; self.i=0; x=r
        else:
            ni=payload
            if not (0<=ni<len(self.rows)):self.next();return
            self.i=ni; x=self.rows[ni]
        self.stream_current=None; self.local_current={"path":x[0],"title":x[1],"artist":x[2],"album":x[3]}
        self.p.setSource(QUrl.fromLocalFile(x[0])); self.p.play()
        QTimer.singleShot(60,lambda pos=carry_pos:self.p.setPosition(pos))
        self.au.setVolume(self.base_volume)
        self.update_playing_indicator(); self.d.played(x[0]); self.now.setText(f"{x[1]}\n{x[2]} • {x[3]}"); self.art(x[0])
        if self.video_player:
            self.video_player.load_current()

    def next(self):
        if self.stream_current and self.stream_rows:
            cur=next((i for i,r in enumerate(self.stream_rows) if r.get("id")==self.stream_current.get("id")),-1)
            self.play_stream((cur+1)%len(self.stream_rows)); return
        if 0<=self.i<len(self.rows) and self.p.duration()>0 and self.p.position()<self.p.duration()*.5:self.d.skip(self.rows[self.i][0])
        if self.queue:
            entry=self.queue.pop(0)
            if isinstance(entry,tuple) and entry[0]=="stream":
                r=self.d.stream(entry[1])
                if r:self.stream_current=r;self.play_stream_dict(r);return
            path=entry[1] if isinstance(entry,tuple) else entry
            r=next((x for x in self.d.songs() if x[0]==path),None)
            if r:self.rows=[r];self.i=0;self.playrow(0);return
        if self.rows:self.i=random.randrange(len(self.rows)) if self.shuffle else (self.i+1)%len(self.rows); self.playrow(self.i)

    def prev(self):
        if self.stream_current and self.stream_rows:
            cur=next((i for i,r in enumerate(self.stream_rows) if r.get("id")==self.stream_current.get("id")),0)
            self.play_stream((cur-1)%len(self.stream_rows)); return
        if self.rows:self.i=(self.i-1)%len(self.rows); self.playrow(self.i)

    def selected(self):
        it=self.list.currentItem(); d=it.data(Qt.ItemDataRole.UserRole) if it else None
        return d if isinstance(d,str) else (self.rows[self.i][0] if 0<=self.i<len(self.rows) else None)

    def favorite(self):
        if self.stream_current and self.p.playbackState()!=QMediaPlayer.PlaybackState.StoppedState:
            self.d.upsert_stream(self.stream_current);self.d.stream_fav(self.stream_current["id"])
            self.statusBar().showMessage("Favorito de streaming actualizado",2200);return
        p=self.selected()
        if p:self.d.fav(p); self.refresh()

    def menu(self,pos):
        it=self.list.itemAt(pos)
        if not it:return
        self.list.setCurrentItem(it);d=it.data(Qt.ItemDataRole.UserRole)
        if isinstance(d,tuple) and d and d[0]=="folder":
            folder=d[1]
            count=sum(1 for r in self.d.songs() if str(r[0]).startswith(str(Path(folder))+os.sep))
            m=QMenu(self)
            opena=m.addAction("Abrir carpeta")
            remove=None
            if count==0:remove=m.addAction("Eliminar carpeta vacía de la colección")
            x=m.exec(self.list.mapToGlobal(pos))
            if x==opena:self.activate();return
            if remove is not None and x==remove:
                ans=QMessageBox.question(self,"Eliminar carpeta vacía",
                    f"¿Quitar esta carpeta de la colección?\n\n{folder}\n\nNo se eliminará ninguna carpeta ni archivo del disco.")
                if ans==QMessageBox.StandardButton.Yes:
                    self.d.remove_folder(folder);self.currentFolder=None;self.render_folders()
                    self.statusBar().showMessage("Carpeta vacía eliminada de la colección",3000)
                return
            return
        if isinstance(d,tuple) and d and d[0]=="queue_item":
            idx=d[1]; m=QMenu(self); playnow=m.addAction("Reproducir ahora"); up=m.addAction("Mover arriba");down=m.addAction("Mover abajo");remove=m.addAction("Quitar de la cola")
            x=m.exec(self.list.mapToGlobal(pos))
            if x==playnow:self.activate()
            elif x==up and idx>0:self.queue[idx-1],self.queue[idx]=self.queue[idx],self.queue[idx-1];self.render_queue()
            elif x==down and idx<len(self.queue)-1:self.queue[idx+1],self.queue[idx]=self.queue[idx],self.queue[idx+1];self.render_queue()
            elif x==remove and 0<=idx<len(self.queue):
                self.queue.pop(idx); self.render_queue(); self.statusBar().showMessage("Canción quitada de la cola",2000)
            return
        is_stream=isinstance(d,tuple) and d and d[0]=="stream"
        p=self.selected() if not is_stream else None
        if not p and not is_stream:return
        m=QMenu(self);play=m.addAction("Reproducir");playnext=m.addAction("Reproducir siguiente");queue=m.addAction("Añadir al final de la cola");fav=m.addAction("Añadir/quitar favorito");pl=m.addAction("Añadir a playlist");lyrics=m.addAction("Ver letra");info=m.addAction("Información")
        rm=None;acts=[]
        vmenu=None;vauto=vchoose=vclear=None
        if not is_stream:
            rm=m.addMenu("Calificación");acts=[rm.addAction("Sin calificación")]+[rm.addAction("★"*i) for i in range(1,6)]
            vmenu=m.addMenu("Vídeo de YouTube");vauto=vmenu.addAction("Buscar automáticamente");vchoose=vmenu.addAction("Elegir otro vídeo");vclear=vmenu.addAction("Quitar asociación")
        x=m.exec(self.list.mapToGlobal(pos))
        if x==play:self.activate()
        elif is_stream:
            row=self.stream_rows[d[1]]
            self.d.upsert_stream(row)
            if x==fav:self.d.stream_fav(row["id"]);self.statusBar().showMessage("Favorito actualizado",2000)
            elif x==queue:
                self.queue.append(("stream",row["id"]));self.statusBar().showMessage(f"Cola: {len(self.queue)} canciones",2500)
                if self.view=="Cola":self.render_queue()
            elif x==pl:self.addpl_stream(row)
        else:
            if x==fav:self.d.fav(p);self.refresh()
            elif x==queue:
                self.queue.append(("local",p));self.statusBar().showMessage(f"Cola: {len(self.queue)} canciones",2500)
                if self.view=="Cola":self.render_queue()
            elif x==pl:self.addpl(p)
            elif x==vauto:
                r=next((r for r in self.d.songs() if r[0]==p),None)
                if r:self.local_current={"path":r[0],"title":r[1],"artist":r[2],"album":r[3]};self.find_local_video(True,lambda z:self.statusBar().showMessage("Vídeo asociado" if z else "No encontré un vídeo adecuado",3000))
            elif x==vchoose:
                r=next((r for r in self.d.songs() if r[0]==p),None)
                if r:self.local_current={"path":r[0],"title":r[1],"artist":r[2],"album":r[3]};self.choose_local_video()
            elif x==vclear:self.d.clear_local_video(p);self.statusBar().showMessage("Asociación de vídeo eliminada",2200)
            elif x in acts:self.d.rate(p,acts.index(x));self.refresh()


    def start_radio(self,artist,title=""):
        q=(artist+" "+title+" canciones similares").strip()
        self.nav("Streaming");self.search.setText(q);self.search_youtube()
        self.statusBar().showMessage("Radio iniciada · preparando canciones relacionadas",3000)

    def show_lyrics(self,artist,title):
        if not artist or not title:
            QMessageBox.information(self,"Letra","No hay suficientes metadatos para buscar la letra.");return
        from urllib.parse import quote
        rep=self.net.get(QNetworkRequest(QUrl(f"https://api.lyrics.ovh/v1/{quote(artist)}/{quote(title)}")))
        self.statusBar().showMessage("Buscando letra…",2000)
        def done():
            try:data=json.loads(bytes(rep.readAll()).decode("utf-8","ignore"));ly=data.get("lyrics","").strip()
            except Exception:ly=""
            rep.deleteLater()
            dlg=QDialog(self);dlg.setWindowTitle(f"Letra · {title}");dlg.resize(650,720);v=QVBoxLayout(dlg)
            h=QLabel(f"{title}\\n{artist}");h.setObjectName("heading");v.addWidget(h)
            box=QTextEdit();box.setReadOnly(True);box.setPlainText(ly or "No encontré una letra disponible para esta canción.");v.addWidget(box,1)
            dlg.exec()
        rep.finished.connect(done)

    def show_track_info(self,row,stream=False):
        if stream:
            text=f"Título: {row.get('title','')}\\nArtista: {row.get('artist','')}\\nFuente: YouTube\\nDuración: {int(row.get('duration',0))//60}:{int(row.get('duration',0))%60:02d}\\nID: {row.get('id','')}"
        else:
            path,title,artist,album,genre,year,duration,fav,rating=row
            try:size=Path(path).stat().st_size/1024/1024
            except Exception:size=0
            text=f"Título: {title}\\nArtista: {artist}\\nÁlbum: {album}\\nAño: {year}\\nGénero: {genre}\\nDuración: {int(duration)//60}:{int(duration)%60:02d}\\nValoración: {rating}/5\\nTamaño: {size:.1f} MB\\nRuta: {path}"
        QMessageBox.information(self,"Información de la canción",text)

    def addpl(self,p):
        opts=self.d.pls()+["+ Nueva playlist"]; n,ok=QInputDialog.getItem(self,"Playlist","Selecciona:",opts,0,False)
        if not ok:return
        if n=="+ Nueva playlist":
            n,ok=QInputDialog.getText(self,"Nueva playlist","Nombre:")
            if not ok or not n.strip():return
            n=n.strip(); self.d.newpl(n)
        self.d.addpl(n,p); self.reload_playlists()

    def addpl_stream(self,row):
        opts=self.d.pls()+["+ Nueva playlist"];n,ok=QInputDialog.getItem(self,"Playlist","Selecciona:",opts,0,False)
        if not ok:return
        if n=="+ Nueva playlist":
            n,ok=QInputDialog.getText(self,"Nueva playlist","Nombre:")
            if not ok or not n.strip():return
            n=n.strip();self.d.newpl(n)
        self.d.upsert_stream(row);self.d.add_stream_pl(n,row["id"]);self.reload_playlists()
        self.statusBar().showMessage(f"Añadida a {n}",2200)

    def pos(self,p):
        self.sl.setValue(p); d=self.p.duration(); f=lambda x:f"{x//60000}:{(x//1000)%60:02d}"; self.tm.setText(f(p)); self.tt.setText(f(d))
        if self.crossfade and not self.stream_current and d>0 and not self.crossfade_started and p>=max(0,d-self.crossfade_ms): self._begin_crossfade()

    def save_session(self):
        try:
            data=json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
            current=None
            if self.stream_current:current={"kind":"stream","id":self.stream_current.get("id")}
            elif self.local_current:current={"kind":"local","path":self.local_current.get("path")}
            data["session"]={"queue":self.queue,"current":current,"position":self.p.position(),"volume":self.base_volume,"view":self.view}
            SETTINGS_FILE.write_text(json.dumps(data))
        except Exception:pass

    def restore_session(self):
        try:data=json.loads(SETTINGS_FILE.read_text()).get("session",{})
        except Exception:return
        q=[]
        for e in data.get("queue",[]):
            if isinstance(e,list) and len(e)>=2:q.append(tuple(e))
            else:q.append(e)
        self.queue=q
        self.base_volume=float(data.get("volume",self.base_volume));self.au.setVolume(self.base_volume)
        cur=data.get("current") or {};pos=int(data.get("position",0))
        if cur.get("kind")=="local" and Path(cur.get("path","")).exists():
            r=next((x for x in self.d.songs() if x[0]==cur["path"]),None)
            if r:
                self.rows=[r];self.i=0;self.playrow(0);self.p.pause();QTimer.singleShot(250,lambda:self.p.setPosition(pos))
        elif cur.get("kind")=="stream":
            r=self.d.stream(cur.get("id",""))
            if r:
                self.play_stream_dict(r);QTimer.singleShot(1500,lambda:self.p.pause());QTimer.singleShot(1700,lambda:self.p.setPosition(pos))
        self.statusBar().showMessage("Sesión anterior recuperada",2200)

    def closeEvent(self,e):
        self.save_session()
        for p in (self.stream_process,self.resolve_process,self.download_process,self.hybrid_process,getattr(self,"_home_trending_process",None),getattr(self,"local_video_search",None)):
            try:
                if p and p.state()!=QProcess.ProcessState.NotRunning:p.kill()
            except Exception:pass
        try:self.dsp.unload()
        except Exception:pass
        e.accept()

    def end(self,s):
        if s==QMediaPlayer.MediaStatus.EndOfMedia:
            if self.crossfade_started:return
            if self.repeat:self.p.setPosition(0); self.p.play()
            else:self.next()


class VideoPlayer(QWidget):
    """Player flotante estable basado en carátula; comparte el audio principal."""
    def __init__(self,main):
        super().__init__();self.main=main
        self.setWindowTitle("Sakukelly · Player");self.setWindowIcon(QIcon(APP_ICON))
        self.setWindowFlags(Qt.WindowType.Tool);self.resize(420,150);self.setMinimumSize(260,110)
        v=QVBoxLayout(self);v.setContentsMargins(10,8,10,8);v.setSpacing(5)
        self.art=QLabel();self.art.hide()
        self.title=QLabel("Nada reproduciéndose");self.title.setAlignment(Qt.AlignmentFlag.AlignCenter);self.title.setWordWrap(True);v.addWidget(self.title)
        c=QHBoxLayout();self.prevb=QPushButton("◀");self.playb=QPushButton("▶ / Ⅱ");self.nextb=QPushButton("▶")
        self.prevb.clicked.connect(main.prev);self.playb.clicked.connect(main.toggle);self.nextb.clicked.connect(main.next)
        c.addStretch();c.addWidget(self.prevb);c.addWidget(self.playb);c.addWidget(self.nextb);c.addStretch();v.addLayout(c)
        self.seek=QSlider(Qt.Orientation.Horizontal);self.seek.setRange(0,1000);self.seek.sliderMoved.connect(self.seek_to);v.addWidget(self.seek)
        self.status=QLabel("Mini-player");self.status.setAlignment(Qt.AlignmentFlag.AlignCenter);v.addWidget(self.status)
        self.pin=QCheckBox("Siempre visible");self.pin.toggled.connect(self.set_always_on_top);v.addWidget(self.pin,0,Qt.AlignmentFlag.AlignCenter)
        self.net=QNetworkAccessManager(self);self.last_key=None
        self.timer=QTimer(self);self.timer.timeout.connect(self.sync);self.timer.start(350);self.sync()

    def set_always_on_top(self,on):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,on);self.show()

    def load_current(self):self.sync(force=True)

    def sync(self,force=False):
        p=self.main.p;d=p.duration()
        if d>0 and not self.seek.isSliderDown():self.seek.setValue(int(p.position()*1000/d))
        if self.main.stream_current:
            r=self.main.stream_current;key="yt:"+str(r.get("id"))
            self.title.setText(f'{r.get("title","")}\n{r.get("artist","")} · YouTube')
            if force or key!=self.last_key:self.last_key=key
        elif self.main.local_current:
            r=self.main.local_current;key="local:"+str(r.get("path"))
            self.title.setText(f'{r.get("title","")}\n{r.get("artist","")} · Local')
            if force or key!=self.last_key:self.last_key=key
        else:self.title.setText("Nada reproduciéndose")

    def set_art(self,px):
        if px is None or px.isNull():
            self.art.setPixmap(QPixmap());self.art.setText("♪");return
        self.art.setText("")
        target=max(72,min(self.art.width(),self.art.height()))
        self.art.setPixmap(px.scaled(target,target,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

    def load_remote_art(self,url,_retry=True):
        if not str(url).startswith("https://"):self.set_art(None);return
        rep=self.net.get(QNetworkRequest(QUrl(url)))
        def done():
            px=QPixmap()
            if px.loadFromData(bytes(rep.readAll())):
                self.set_art(px)
            elif _retry and self.main.stream_current:
                fallback=youtube_thumb_url(self.main.stream_current.get("id"))
                if fallback and fallback!=url:self.load_remote_art(fallback,_retry=False)
                else:self.set_art(None)
            else:self.set_art(None)
            rep.deleteLater()
        rep.finished.connect(done)

    def seek_to(self,x):
        d=self.main.p.duration()
        if d>0:self.main.p.setPosition(int(d*x/1000))


def main():
    app=QApplication(sys.argv)
    # Keep the Linux desktop identity identical to sakukelly.desktop. This lets
    # Cinnamon/X11/Wayland associate the running window with its launcher icon.
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("Sakukelly Project")
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon(APP_ICON))
    w=Win(); w.show(); sys.exit(app.exec())

if __name__=="__main__":
    main()
