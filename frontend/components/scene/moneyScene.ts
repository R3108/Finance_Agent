/**
 * The 3D "money" scene behind the landing hero and the sign-in pitch panel: two payment cards and a
 * ring of ₹ coins floating in a light particle field, drifting toward the pointer.
 *
 * Plain three.js (no React renderer) so the whole thing is one lazily-loaded chunk that Scene3D
 * imports only once its box scrolls into view. Every texture is drawn on a canvas at start-up, so
 * there are no image requests and nothing to cache-bust. The caller owns the render loop through
 * start/stop (it pauses offscreen and in background tabs) and must call dispose() on unmount —
 * browsers cap live WebGL contexts, and a leaked one per navigation runs out quickly.
 */
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

export type SceneVariant = "hero" | "auth";
export type SceneOptions = { variant: SceneVariant; accent: string; reducedMotion: boolean; pointer: boolean };
export type SceneHandle = { start: () => void; stop: () => void; dispose: () => void };

type Palette = { from: string; to: string; ink: string; soft: string };

const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';
const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
const CARD_W = 3.4;
const CARD_H = 2.14;          // ISO/IEC 7810 ID-1 proportions
const TEX_W = 1024;
const TEX_H = 644;
const INTRO_S = 1.8;

const clamp01 = (t: number) => Math.min(1, Math.max(0, t));
const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);
const easeOutBack = (t: number) => 1 + 2.2 * Math.pow(t - 1, 3) + 1.2 * Math.pow(t - 1, 2);

function canvasTexture(w: number, h: number, draw: (ctx: CanvasRenderingContext2D) => void, anisotropy: number) {
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  draw(canvas.getContext("2d")!);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = anisotropy;
  return tex;
}

function palettes(accent: string): Record<"accent" | "ink" | "frost", Palette> {
  const base = new THREE.Color(accent);
  return {
    accent: {
      from: `#${base.clone().lerp(new THREE.Color("#ffffff"), 0.18).getHexString()}`,
      to: `#${base.clone().lerp(new THREE.Color("#050a1a"), 0.45).getHexString()}`,
      ink: "#ffffff", soft: "rgba(255,255,255,0.09)",
    },
    ink: { from: "#2a3142", to: "#07090d", ink: "#f4f1e8", soft: "rgba(255,255,255,0.06)" },
    frost: { from: "#ffffff", to: "#d5e2f3", ink: "#10213a", soft: "rgba(16,33,58,0.07)" },
  };
}

function cardBody(ctx: CanvasRenderingContext2D, p: Palette) {
  const body = ctx.createLinearGradient(0, 0, TEX_W, TEX_H);
  body.addColorStop(0, p.from);
  body.addColorStop(1, p.to);
  ctx.fillStyle = body;
  ctx.fillRect(0, 0, TEX_W, TEX_H);

  const glow = ctx.createRadialGradient(TEX_W * 0.82, -TEX_H * 0.1, 0, TEX_W * 0.82, -TEX_H * 0.1, TEX_W * 0.75);
  glow.addColorStop(0, "rgba(255,255,255,0.32)");
  glow.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, TEX_W, TEX_H);

  // guilloché: the fine interference waves printed on banknotes and cards
  ctx.lineWidth = 2;
  ctx.strokeStyle = p.soft;
  for (let i = 0; i < 16; i++) {
    ctx.beginPath();
    for (let x = 0; x <= TEX_W; x += 8) {
      const y = TEX_H * 0.42 + i * 16 + Math.sin(x / 110 + i * 0.45) * 34;
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
}

function drawCardFront(ctx: CanvasRenderingContext2D, p: Palette) {
  cardBody(ctx, p);

  ctx.fillStyle = p.ink;
  ctx.beginPath();
  ctx.roundRect(72, 64, 68, 68, 18);
  ctx.fill();
  ctx.fillStyle = p.to;
  ctx.font = `800 44px ${FONT}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("L", 106, 100);
  ctx.textAlign = "left";
  ctx.fillStyle = p.ink;
  ctx.font = `700 46px ${FONT}`;
  ctx.fillText("Ledgerly", 160, 100);
  ctx.textAlign = "right";
  ctx.globalAlpha = 0.75;
  ctx.font = `700 24px ${FONT}`;
  ctx.fillText("VIRTUAL", TEX_W - 72, 100);
  ctx.globalAlpha = 1;
  ctx.textAlign = "left";

  // EMV chip
  const chip = ctx.createLinearGradient(80, 228, 216, 332);
  chip.addColorStop(0, "#f6dc97");
  chip.addColorStop(0.5, "#d7a948");
  chip.addColorStop(1, "#b98a2e");
  ctx.fillStyle = chip;
  ctx.beginPath();
  ctx.roundRect(80, 228, 136, 104, 18);
  ctx.fill();
  ctx.strokeStyle = "rgba(90,60,10,0.45)";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(80, 263); ctx.lineTo(216, 263);
  ctx.moveTo(80, 297); ctx.lineTo(216, 297);
  ctx.moveTo(148, 228); ctx.lineTo(148, 263);
  ctx.moveTo(148, 297); ctx.lineTo(148, 332);
  ctx.stroke();

  // contactless waves
  ctx.strokeStyle = p.ink;
  ctx.lineWidth = 6;
  ctx.lineCap = "round";
  for (let i = 0; i < 3; i++) {
    ctx.beginPath();
    ctx.arc(250, 280, 22 + i * 18, -Math.PI / 4, Math.PI / 4);
    ctx.stroke();
  }

  ctx.fillStyle = p.ink;
  ctx.textBaseline = "alphabetic";
  ctx.font = `600 52px ${MONO}`;
  ctx.fillText("••••  ••••  ••••  2026", 80, 462);
  ctx.globalAlpha = 0.7;
  ctx.font = `600 22px ${FONT}`;
  ctx.fillText("CARD HOLDER", 80, 540);
  ctx.fillText("VALID THRU", 690, 540);
  ctx.globalAlpha = 1;
  ctx.font = `600 34px ${FONT}`;
  ctx.fillText("LEDGERLY MEMBER", 80, 584);
  ctx.fillText("09/30", 690, 584);
}

/** Drawn mirrored: the back lid's UVs run the same way as the front's, so seen from behind they flip. */
function drawCardBack(ctx: CanvasRenderingContext2D, p: Palette) {
  ctx.translate(TEX_W, 0);
  ctx.scale(-1, 1);
  cardBody(ctx, p);
  ctx.fillStyle = "rgba(0,0,0,0.78)";
  ctx.fillRect(0, 70, TEX_W, 118);
  ctx.fillStyle = "rgba(255,255,255,0.9)";
  ctx.beginPath();
  ctx.roundRect(72, 250, 600, 84, 8);
  ctx.fill();
  ctx.strokeStyle = "rgba(0,0,0,0.08)";
  ctx.lineWidth = 3;
  for (let x = 72; x < 672; x += 18) {
    ctx.beginPath(); ctx.moveTo(x, 250); ctx.lineTo(x + 30, 334); ctx.stroke();
  }
  ctx.fillStyle = "#111";
  ctx.font = `italic 600 34px ${FONT}`;
  ctx.fillText("•••", 700, 306);
  ctx.fillStyle = p.ink;
  ctx.globalAlpha = 0.6;
  ctx.font = `500 22px ${FONT}`;
  ctx.fillText("Every rupee tracked. Every number checked.", 72, 560);
  ctx.globalAlpha = 1;
}

/** Card: a rounded-rectangle slab with a hairline bevel, front and back lids textured separately. */
function makeCard(p: Palette, aniso: number, disposables: { dispose: () => void }[]) {
  const r = 0.16;
  const w = CARD_W / 2 - r;
  const h = CARD_H / 2 - r;
  const shape = new THREE.Shape();
  shape.moveTo(-w, -CARD_H / 2);
  shape.lineTo(w, -CARD_H / 2);
  shape.quadraticCurveTo(CARD_W / 2, -CARD_H / 2, CARD_W / 2, -h);
  shape.lineTo(CARD_W / 2, h);
  shape.quadraticCurveTo(CARD_W / 2, CARD_H / 2, w, CARD_H / 2);
  shape.lineTo(-w, CARD_H / 2);
  shape.quadraticCurveTo(-CARD_W / 2, CARD_H / 2, -CARD_W / 2, h);
  shape.lineTo(-CARD_W / 2, -h);
  shape.quadraticCurveTo(-CARD_W / 2, -CARD_H / 2, -w, -CARD_H / 2);

  const depth = 0.03;
  const geo = new THREE.ExtrudeGeometry(shape, {
    depth, bevelEnabled: true, bevelThickness: 0.012, bevelSize: 0.012, bevelSegments: 3, curveSegments: 10,
  });
  geo.translate(0, 0, -depth / 2);

  // Extrude's lid UVs are in shape units; map them to 0–1 across the card face.
  const pos = geo.attributes.position;
  const uv = geo.attributes.uv;
  for (let i = 0; i < pos.count; i++) {
    uv.setXY(i, (pos.getX(i) + CARD_W / 2) / CARD_W, (pos.getY(i) + CARD_H / 2) / CARD_H);
  }
  uv.needsUpdate = true;

  // Group 0 holds both lids — bottom (back) first, then top (front). Split it so each gets its own face.
  const lids = geo.groups[0];
  const half = lids.count / 2;
  const sides = geo.groups[1];
  geo.clearGroups();
  geo.addGroup(lids.start, half, 2);
  geo.addGroup(lids.start + half, half, 0);
  geo.addGroup(sides.start, sides.count, 1);

  const front = canvasTexture(TEX_W, TEX_H, (c) => drawCardFront(c, p), aniso);
  const back = canvasTexture(TEX_W, TEX_H, (c) => drawCardBack(c, p), aniso);
  // A modest env intensity keeps the printed face readable; the clearcoat still catches highlights.
  const face = { roughness: 0.4, metalness: 0.05, clearcoat: 0.8, clearcoatRoughness: 0.08, envMapIntensity: 0.55 };
  const mats = [
    new THREE.MeshPhysicalMaterial({ ...face, map: front }),
    new THREE.MeshPhysicalMaterial({ color: p.to, roughness: 0.3, metalness: 0.55, clearcoat: 1 }),
    new THREE.MeshPhysicalMaterial({ ...face, map: back }),
  ];
  disposables.push(geo, front, back, ...mats);
  return new THREE.Mesh(geo, mats);
}

function makeCoinMaterials(aniso: number, disposables: { dispose: () => void }[]) {
  const face = canvasTexture(512, 512, (ctx) => {
    const g = ctx.createRadialGradient(200, 180, 20, 256, 256, 256);
    g.addColorStop(0, "#fff1c2");
    g.addColorStop(0.55, "#e3ad45");
    g.addColorStop(1, "#9c6b1c");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 512, 512);
    ctx.strokeStyle = "rgba(110,70,15,0.55)";
    ctx.lineWidth = 14;
    ctx.beginPath(); ctx.arc(256, 256, 222, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = "rgba(110,70,15,0.45)";
    for (let i = 0; i < 48; i++) {
      const a = (i / 48) * Math.PI * 2;
      ctx.beginPath(); ctx.arc(256 + Math.cos(a) * 198, 256 + Math.sin(a) * 198, 4, 0, Math.PI * 2); ctx.fill();
    }
    // A cylinder cap's UVs are turned a quarter relative to the coin once it faces the camera, so
    // the glyph is drawn turned back the other way.
    ctx.translate(256, 256);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = `800 290px ${FONT}`;
    ctx.fillStyle = "rgba(100,62,10,0.6)";
    ctx.fillText("₹", 7, 17);
    const glyph = ctx.createLinearGradient(-80, -120, 80, 120);
    glyph.addColorStop(0, "#fff6d8");
    glyph.addColorStop(1, "#d39a33");
    ctx.fillStyle = glyph;
    ctx.fillText("₹", 0, 10);
  }, aniso);

  const edge = canvasTexture(64, 8, (ctx) => {
    ctx.fillStyle = "#d9a441";
    ctx.fillRect(0, 0, 64, 8);
    ctx.fillStyle = "#8f621b";
    for (let x = 0; x < 64; x += 8) ctx.fillRect(x, 0, 3, 8);   // reeded edge
  }, aniso);
  edge.wrapS = THREE.RepeatWrapping;
  edge.repeat.set(12, 1);

  const gold = { metalness: 1, roughness: 0.3 };
  const mats = [
    new THREE.MeshStandardMaterial({ ...gold, map: edge }),
    new THREE.MeshStandardMaterial({ ...gold, map: face }),
    new THREE.MeshStandardMaterial({ ...gold, map: face }),
  ];
  disposables.push(face, edge, ...mats);
  return mats;
}

function makeParticles(count: number, color: string, disposables: { dispose: () => void }[]) {
  const positions = new Float32Array(count * 3);
  const speeds = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    positions[i * 3] = (Math.random() - 0.5) * 16;
    positions[i * 3 + 1] = (Math.random() - 0.5) * 10;
    positions[i * 3 + 2] = -6 + Math.random() * 7;
    speeds[i] = 0.08 + Math.random() * 0.22;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const dot = canvasTexture(64, 64, (ctx) => {
    const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(0.4, "rgba(255,255,255,0.55)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 64, 64);
  }, 1);
  const mat = new THREE.PointsMaterial({
    color, map: dot, size: 0.09, transparent: true, opacity: 0.75, depthWrite: false, sizeAttenuation: true,
  });
  disposables.push(geo, dot, mat);
  return { points: new THREE.Points(geo, mat), speeds };
}

/** Returns null when WebGL isn't available (old GPU, blocked, too many contexts) — the page keeps its 2D look. */
export function mountMoneyScene(host: HTMLElement, opts: SceneOptions): SceneHandle | null {
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
  } catch {
    return null;
  }
  const hero = opts.variant === "hero";
  const disposables: { dispose: () => void }[] = [];

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  // Neutral, not ACES: ACES desaturates mid-tones and turned the brand-blue card powder blue.
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.toneMappingExposure = 1;
  renderer.setClearColor(0x000000, 0);
  const canvas = renderer.domElement;
  canvas.style.cssText = "display:block;width:100%;height:100%";
  host.appendChild(canvas);

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const envMap = pmrem.fromScene(room, 0.04).texture;
  room.dispose();
  pmrem.dispose();
  scene.environment = envMap;
  disposables.push(envMap);

  const key = new THREE.DirectionalLight(0xffffff, 1.6);
  key.position.set(3, 5, 6);
  scene.add(key, new THREE.AmbientLight(0xffffff, 0.35));

  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100);
  const aniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  const pal = palettes(opts.accent);

  // rig: follows the pointer; stage: holds the composition and is offset per variant
  const rig = new THREE.Group();
  const stage = new THREE.Group();
  rig.add(stage);
  scene.add(rig);

  const frontCard = makeCard(hero ? pal.accent : pal.frost, aniso, disposables);
  const backCard = makeCard(pal.ink, aniso, disposables);
  const frontRest = new THREE.Euler(-0.14, -0.3, 0.06);
  const backRest = new THREE.Euler(-0.12, -0.62, 0.2);
  const backHome = new THREE.Vector3(0.75, 0.62, -0.9);
  stage.add(backCard, frontCard);

  const coinGeo = new THREE.CylinderGeometry(0.46, 0.46, 0.085, 64);
  disposables.push(coinGeo);
  const coinMats = makeCoinMaterials(aniso, disposables);
  const coinCount = hero ? 7 : 5;
  const coins = Array.from({ length: coinCount }, (_, i) => {
    const mesh = new THREE.Mesh(coinGeo, coinMats);
    const coin = {
      mesh,
      phase: (i / coinCount) * Math.PI * 2,
      radius: 2.45 + (i % 2) * 0.35,
      lift: (i % 3) * 0.18 - 0.18,
      size: 0.72 + ((i * 37) % 10) / 30,
      spin: (i % 2 ? 1 : -1) * (0.9 + (i % 3) * 0.25),
      tilt: 0.25 + (i % 4) * 0.12,
    };
    mesh.rotation.x = Math.PI / 2;
    stage.add(mesh);
    return coin;
  });

  // a hairline orbit ring so the coins read as circling the cards, not scattered
  const orbitGeo = new THREE.TorusGeometry(2.6, 0.006, 8, 160);
  const orbitMat = new THREE.MeshBasicMaterial({ color: hero ? opts.accent : "#ffffff", transparent: true, opacity: hero ? 0.35 : 0.3 });
  disposables.push(orbitGeo, orbitMat);
  const orbit = new THREE.Mesh(orbitGeo, orbitMat);
  orbit.rotation.x = Math.PI / 2 - 0.36;
  stage.add(orbit);

  const dust = makeParticles(hero ? 170 : 120, hero ? opts.accent : "#ffffff", disposables);
  scene.add(dust.points);

  // ---------------------------------------------------------------- sizing
  let width = 1;
  let height = 1;
  const resize = () => {
    width = Math.max(1, host.clientWidth);
    height = Math.max(1, host.clientHeight);
    renderer.setSize(width, height, false);
    const aspect = width / height;
    camera.aspect = aspect;
    // Keep the whole composition (≈6.4 × 4.6 units) in frame whatever the box's shape.
    const fit = Math.max(7.6, 10.4 / aspect);
    camera.position.set(0, 0, hero ? fit : 10.5);
    camera.updateProjectionMatrix();
    if (!hero) {
      // The pitch panel's copy fills the upper left; park the objects below it and to the right,
      // partly off-edge, so no line of text ever sits on top of the card.
      const visH = 2 * camera.position.z * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
      const visW = visH * aspect;
      stage.position.set(visW * 0.45, -visH * 0.3, 0);
      stage.scale.setScalar(0.82);
    }
  };
  resize();
  const ro = new ResizeObserver(() => {
    resize();
    if (!running) render(elapsed);
  });
  ro.observe(host);

  // ---------------------------------------------------------------- input
  const target = { x: 0, y: 0, scroll: 0 };
  const onPointer = (e: PointerEvent) => {
    target.x = (e.clientX / window.innerWidth) * 2 - 1;
    target.y = (e.clientY / window.innerHeight) * 2 - 1;
  };
  const onScroll = () => { target.scroll = clamp01(window.scrollY / 700); };
  if (opts.pointer && !opts.reducedMotion) window.addEventListener("pointermove", onPointer, { passive: true });
  if (hero && !opts.reducedMotion) window.addEventListener("scroll", onScroll, { passive: true });

  // ---------------------------------------------------------------- frame
  const dustPos = dust.points.geometry.attributes.position as THREE.BufferAttribute;
  let elapsed = opts.reducedMotion ? INTRO_S + 2 : 0;
  const render = (t: number, dt = 0) => {
    const intro = clamp01(t / INTRO_S);
    const e = easeOutCubic(intro);
    const pop = easeOutBack(clamp01((t - 0.1) / (INTRO_S * 0.8)));

    frontCard.position.set(0, (1 - e) * -0.8 + Math.sin(t * 0.9) * 0.07, 0);
    frontCard.rotation.set(
      frontRest.x + Math.sin(t * 0.7) * 0.04,
      frontRest.y - (1 - e) * 2.4 + Math.sin(t * 0.5) * 0.06,
      frontRest.z,
    );
    frontCard.scale.setScalar(0.6 + 0.4 * pop);

    const eb = easeOutCubic(clamp01((t - 0.25) / INTRO_S));
    backCard.position.set(backHome.x * eb, backHome.y * eb + Math.sin(t * 0.8 + 1.3) * 0.06, backHome.z);
    backCard.rotation.set(backRest.x, backRest.y + (1 - eb) * 1.6 + Math.sin(t * 0.45 + 2) * 0.05, backRest.z);
    backCard.scale.setScalar(0.92 * (0.5 + 0.5 * eb));

    for (const [i, c] of coins.entries()) {
      const ce = easeOutCubic(clamp01((t - 0.35 - i * 0.07) / 1.3));
      const a = c.phase + t * 0.28;
      const r = c.radius * ce;
      c.mesh.position.set(Math.cos(a) * r, Math.sin(a) * r * 0.36 + c.lift + Math.sin(t * 1.1 + i) * 0.06, Math.sin(a) * r * 0.9);
      c.mesh.rotation.set(Math.PI / 2 - c.tilt, t * c.spin, 0, "YXZ");
      c.mesh.scale.setScalar(c.size * easeOutBack(clamp01((t - 0.35 - i * 0.07) / 1.1)));
    }
    orbitMat.opacity = (hero ? 0.35 : 0.3) * e;

    if (dt > 0) {
      for (let i = 0; i < dustPos.count; i++) {
        let y = dustPos.getY(i) + dust.speeds[i] * dt;
        if (y > 5) y = -5;
        dustPos.setY(i, y);
      }
      dustPos.needsUpdate = true;
    }

    // ease toward the pointer and (hero) tip back a little as the page scrolls away
    rig.rotation.y += (target.x * 0.32 - rig.rotation.y) * 0.05;
    rig.rotation.x += (target.y * 0.2 + target.scroll * 0.45 - rig.rotation.x) * 0.05;
    renderer.render(scene, camera);
  };

  // ---------------------------------------------------------------- loop
  let running = false;
  let last = 0;
  const tick = (now: number) => {
    const dt = Math.min(0.1, (now - last) / 1000);   // clamp: one long hitch doesn't skip the intro
    last = now;
    elapsed += dt;
    render(elapsed, dt);
  };

  let lost = false;
  const onLost = (e: Event) => { e.preventDefault(); lost = true; renderer.setAnimationLoop(null); };
  canvas.addEventListener("webglcontextlost", onLost);

  return {
    start() {
      if (lost) return;
      if (opts.reducedMotion) { render(elapsed); return; }   // one still frame, no loop
      if (running) return;
      running = true;
      last = performance.now();
      renderer.setAnimationLoop(tick);
    },
    stop() {
      running = false;
      renderer.setAnimationLoop(null);
    },
    dispose() {
      running = false;
      renderer.setAnimationLoop(null);
      ro.disconnect();
      window.removeEventListener("pointermove", onPointer);
      window.removeEventListener("scroll", onScroll);
      canvas.removeEventListener("webglcontextlost", onLost);
      for (const d of disposables) d.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      canvas.remove();
    },
  };
}
