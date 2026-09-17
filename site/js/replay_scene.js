const canvas = document.getElementById("replay-canvas");
const stage = document.getElementById("replay-stage");
const scrubber = document.getElementById("replay-time");
const playButton = document.getElementById("replay-play");
const speedSelect = document.getElementById("replay-speed");
const clockLabel = document.getElementById("replay-clock");
const runningLabel = document.getElementById("replay-running");
const cacheLabel = document.getElementById("replay-cache");
const modeButtons = Array.from(document.querySelectorAll("[data-replay-mode]"));

const state = {
  data: null,
  mode: "physical",
  playing: false,
  playheadMs: 0,
  lastFrameAt: 0,
  rendererKind: "pending",
};

let renderFrame = () => {};
let rebuild = () => {};
let resize = () => {};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function lerp(a, b, amount) {
  return a + (b - a) * amount;
}

function sceneSpan() {
  return Math.max(1, Number((state.data && state.data.source && state.data.source.span_ms) || 1));
}

function frameAt(timeMs) {
  const frames = (state.data && state.data.frames) || [];
  if (!frames.length) return null;
  const index = Math.round((timeMs / sceneSpan()) * (frames.length - 1));
  return frames[clamp(index, 0, frames.length - 1)];
}

function formatTime(milliseconds) {
  const value = Math.max(0, Number(milliseconds) || 0);
  if (value < 1000) return Math.round(value) + " ms";
  if (value < 60000) return (value / 1000).toFixed(value < 10000 ? 1 : 0) + " s";
  return (value / 60000).toFixed(1) + " min";
}

function updateReadout() {
  const frame = frameAt(state.playheadMs);
  clockLabel.textContent = "t+" + formatTime(state.playheadMs);
  runningLabel.textContent = Math.round(Number((frame && frame.running_estimate) || 0)) + " running";
  cacheLabel.textContent = Math.round(Number((frame && frame.cache_pressure) || 0) * 100) + "% cache pressure";
  scrubber.value = String(Math.round((state.playheadMs / sceneSpan()) * 1000));
}

function setPlaying(next) {
  state.playing = Boolean(next);
  playButton.innerHTML = state.playing ? "&#10074;&#10074;" : "&#9654;";
  playButton.setAttribute("aria-label", state.playing ? "Pause replay" : "Play replay");
  playButton.title = state.playing ? "Pause replay" : "Play replay";
}

function setMode(mode) {
  state.mode = mode === "logical" ? "logical" : "physical";
  modeButtons.forEach((button) => {
    const active = button.dataset.replayMode === state.mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  rebuild();
  renderFrame(state.playheadMs);
}

function requestProgress(request, timeMs) {
  const arrival = Number(request.arrival_ms || 0);
  const suppliedEnd = Number(request.completion_ms || arrival);
  const visualEnd = Math.max(suppliedEnd, arrival + sceneSpan() * 0.025);
  if (timeMs < arrival || timeMs > visualEnd) return null;
  return clamp((timeMs - arrival) / Math.max(1, visualEnd - arrival), 0, 1);
}

function physicalPoint(request, progress) {
  const points = [
    [-7, 0, 0],
    [-4.2, 0, 0],
    [-1.4, 1.1, 0],
    [-1.4, -1.5, 0],
    [2, 0, 0],
    [5.4, 0, 0],
  ];
  const scaled = progress * (points.length - 1);
  const index = Math.min(points.length - 2, Math.floor(scaled));
  const amount = scaled - index;
  const from = points[index];
  const to = points[index + 1];
  return [
    lerp(from[0], to[0], amount),
    lerp(from[1], to[1], amount) + Math.sin(progress * Math.PI) * 0.35,
    lerp(from[2], to[2], amount) + ((request.replica || 0) % 5 - 2) * 0.12,
  ];
}

function logicalPoint(request, progress, nodePositions) {
  const target = nodePositions.get(request.prefix_id) || [0, -3.3, -1];
  if (progress < 0.58) {
    const amount = progress / 0.58;
    return [
      lerp(0, target[0], amount),
      lerp(0, target[1], amount) + Math.sin(amount * Math.PI) * 0.7,
      lerp(0, target[2], amount),
    ];
  }
  const orbit = ((progress - 0.58) / 0.42) * Math.PI * 2 + request.index * 0.37;
  const radius = 0.38 + Math.min(0.65, Math.log2(Math.max(2, request.input_tokens)) / 20);
  return [
    target[0] + Math.cos(orbit) * radius,
    target[1] + Math.sin(orbit) * radius * 0.48,
    target[2] + Math.sin(orbit * 0.7) * radius,
  ];
}

function receiveScene(sceneData) {
  state.data = sceneData;
  state.playheadMs = 0;
  setPlaying(false);
  rebuild();
  updateReadout();
  renderFrame(0);
}

window.addEventListener("tracerator:replay-scene", (event) => {
  receiveScene(event.detail);
});

playButton.addEventListener("click", () => {
  if (!state.data) return;
  if (state.playheadMs >= sceneSpan()) state.playheadMs = 0;
  setPlaying(!state.playing);
});

scrubber.addEventListener("input", () => {
  state.playheadMs = (Number(scrubber.value) / 1000) * sceneSpan();
  setPlaying(false);
  updateReadout();
  renderFrame(state.playheadMs);
});

modeButtons.forEach((button) => {
  button.addEventListener("click", () => setMode(button.dataset.replayMode));
});

function animationLoop(now) {
  const delta = state.lastFrameAt ? now - state.lastFrameAt : 0;
  state.lastFrameAt = now;
  if (state.playing && state.data) {
    const speed = Number(speedSelect.value || 1);
    state.playheadMs += delta * (sceneSpan() / 45000) * speed;
    if (state.playheadMs >= sceneSpan()) {
      state.playheadMs = sceneSpan();
      setPlaying(false);
    }
    updateReadout();
  }
  renderFrame(state.playheadMs);
  requestAnimationFrame(animationLoop);
}

function initializeCanvasFallback() {
  const context = canvas.getContext("2d");
  state.rendererKind = "canvas";
  let nodePositions = new Map();

  resize = () => {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const rect = stage.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(rect.width * ratio));
    canvas.height = Math.max(1, Math.round(rect.height * ratio));
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
  };

  rebuild = () => {
    nodePositions = new Map();
    const nodes = state.mode === "logical"
      ? (((state.data || {}).views || {}).logical || {}).nodes || []
      : (((state.data || {}).views || {}).physical || {}).nodes || [];
    nodes.forEach((node) => nodePositions.set(node.id, node.position));
  };

  renderFrame = (timeMs) => {
    const rect = stage.getBoundingClientRect();
    context.clearRect(0, 0, rect.width, rect.height);
    context.fillStyle = "#e9eef2";
    context.fillRect(0, 0, rect.width, rect.height);
    if (!state.data) return;
    const scale = Math.min(rect.width / 15, rect.height / 8);
    const project = (point) => [
      rect.width / 2 + point[0] * scale,
      rect.height / 2 - point[1] * scale,
    ];
    const nodes = state.mode === "logical"
      ? state.data.views.logical.nodes
      : state.data.views.physical.nodes;
    context.lineWidth = 1;
    context.strokeStyle = "#a6b0ba";
    context.beginPath();
    if (state.mode === "physical") {
      const physical = nodes.map((node) => project(node.position));
      physical.forEach((point, index) => {
        if (index === 0) context.moveTo(point[0], point[1]);
        else context.lineTo(point[0], point[1]);
      });
    } else {
      const center = project([0, 0, 0]);
      nodes.filter((node) => node.id !== "workload").forEach((node) => {
        const point = project(node.position);
        context.moveTo(center[0], center[1]);
        context.lineTo(point[0], point[1]);
      });
    }
    context.stroke();

    nodes.forEach((node) => {
      const point = project(node.position);
      context.fillStyle = node.kind === "memory" ? "#24a148"
        : node.kind === "queue" ? "#f1c21b"
          : node.kind === "prefix" ? "#0f62fe"
            : "#525252";
      context.fillRect(point[0] - 16, point[1] - 12, 32, 24);
      context.fillStyle = "#0b1220";
      context.font = "11px IBM Plex Sans";
      context.textAlign = "center";
      context.fillText(node.label, point[0], point[1] + 30);
    });

    (state.data.requests || []).forEach((request) => {
      const progress = requestProgress(request, timeMs);
      if (progress == null) return;
      const point3d = state.mode === "logical"
        ? logicalPoint(request, progress, nodePositions)
        : physicalPoint(request, progress);
      const point = project(point3d);
      context.beginPath();
      context.arc(point[0], point[1], 2.5 + request.hit_share * 2.5, 0, Math.PI * 2);
      context.fillStyle = request.hit_share > 0.5 ? "#24a148" : "#0f62fe";
      context.fill();
    });
  };

  resize();
}

async function initializeThree() {
  const [threeModule, controlsModule] = await Promise.all([
    import("https://esm.sh/three@0.180.0"),
    import("https://esm.sh/three@0.180.0/examples/jsm/controls/OrbitControls.js"),
  ]);
  const THREE = threeModule;
  const OrbitControls = controlsModule.OrbitControls;
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: false,
    powerPreference: "high-performance",
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0xe9eef2, 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const world = new THREE.Scene();
  world.fog = new THREE.Fog(0xe9eef2, 14, 26);
  const camera = new THREE.PerspectiveCamera(46, 1, 0.1, 80);
  camera.position.set(0, 5.5, 13.5);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 7;
  controls.maxDistance = 24;
  controls.target.set(-0.5, 0, 0);

  world.add(new THREE.HemisphereLight(0xffffff, 0x9aa6b1, 2.4));
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.6);
  keyLight.position.set(4, 8, 7);
  world.add(keyLight);
  const grid = new THREE.GridHelper(22, 22, 0xaab4bd, 0xd3d9de);
  grid.position.y = -2.55;
  world.add(grid);

  let graphGroup = new THREE.Group();
  let particleGroup = new THREE.Group();
  let nodePositions = new Map();
  let particleMeshes = [];
  let cacheMesh = null;
  world.add(graphGroup);
  world.add(particleGroup);

  function labelSprite(text, compact) {
    const labelCanvas = document.createElement("canvas");
    labelCanvas.width = 256;
    labelCanvas.height = 64;
    const labelContext = labelCanvas.getContext("2d");
    labelContext.clearRect(0, 0, 256, 64);
    labelContext.fillStyle = "#0b1220";
    labelContext.font = "600 25px IBM Plex Sans";
    labelContext.textAlign = "center";
    labelContext.fillText(text, 128, 38);
    const texture = new THREE.CanvasTexture(labelCanvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const material = new THREE.SpriteMaterial({ map: texture, transparent: true });
    const sprite = new THREE.Sprite(material);
    sprite.scale.set(compact ? 1.5 : 2.8, compact ? 0.48 : 0.7, 1);
    return sprite;
  }

  function nodeColor(kind) {
    if (kind === "memory") return 0x24a148;
    if (kind === "queue") return 0xf1c21b;
    if (kind === "compute") return 0x0f62fe;
    if (kind === "prefix") return 0x4589ff;
    return 0x525252;
  }

  function disposeGroup(group) {
    group.traverse((object) => {
      if (object.geometry) object.geometry.dispose();
      if (object.material) {
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        materials.forEach((material) => {
          if (material.map) material.map.dispose();
          material.dispose();
        });
      }
    });
    world.remove(group);
  }

  rebuild = () => {
    disposeGroup(graphGroup);
    disposeGroup(particleGroup);
    graphGroup = new THREE.Group();
    particleGroup = new THREE.Group();
    world.add(graphGroup);
    world.add(particleGroup);
    particleMeshes = [];
    nodePositions = new Map();
    cacheMesh = null;
    if (!state.data) return;

    const view = state.mode === "logical" ? state.data.views.logical : state.data.views.physical;
    const nodes = view.nodes || [];
    nodes.forEach((node) => {
      nodePositions.set(node.id, node.position);
      const scale = node.kind === "prefix"
        ? 0.55 + Math.min(0.7, Math.sqrt(Number(node.request_count || 1)) / 20)
        : 0.8;
      const geometry = node.kind === "prefix"
        ? new THREE.SphereGeometry(scale, 24, 16)
        : new THREE.BoxGeometry(1.35, 0.85, 0.85);
      const material = new THREE.MeshStandardMaterial({
        color: nodeColor(node.kind),
        roughness: 0.58,
        metalness: node.kind === "memory" ? 0.18 : 0.05,
        transparent: node.kind === "prefix",
        opacity: node.kind === "prefix" ? 0.86 : 1,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.fromArray(node.position);
      mesh.userData.nodeId = node.id;
      graphGroup.add(mesh);
      if (node.id === "cache") cacheMesh = mesh;
      const label = labelSprite(node.label, state.mode === "logical");
      if (state.mode === "logical" && node.id !== "workload") {
        const outward = 1.18;
        label.position.set(
          node.position[0] * outward,
          node.position[1] * outward + (node.position[1] >= 0 ? 0.35 : -0.15),
          node.position[2],
        );
      } else {
        label.position.set(node.position[0], node.position[1] + scale + 0.65, node.position[2]);
      }
      graphGroup.add(label);
    });

    const edges = view.edges || [];
    edges.forEach((edge) => {
      const from = nodePositions.get(edge[0]) || [0, 0, 0];
      const to = nodePositions.get(edge[1]) || [0, 0, 0];
      const geometry = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(...from),
        new THREE.Vector3(...to),
      ]);
      const line = new THREE.Line(
        geometry,
        new THREE.LineBasicMaterial({ color: 0x9aa6b1, transparent: true, opacity: 0.7 }),
      );
      graphGroup.add(line);
    });

    const requests = (state.data.requests || []).slice(0, 240);
    requests.forEach((request) => {
      const radius = 0.07 + Math.min(0.09, Math.log2(Math.max(2, request.input_tokens)) / 110);
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(radius, 10, 8),
        new THREE.MeshStandardMaterial({
          color: request.hit_share > 0.5 ? 0x24a148 : 0x0f62fe,
          emissive: request.hit_share > 0.5 ? 0x0f4c24 : 0x002d6f,
          emissiveIntensity: 0.7,
          roughness: 0.25,
        }),
      );
      mesh.visible = false;
      mesh.userData.request = request;
      particleMeshes.push(mesh);
      particleGroup.add(mesh);
    });

    if (state.mode === "logical") {
      camera.position.set(0, 9.5, 15);
      controls.target.set(0, 0, 0);
    } else {
      camera.position.set(0, 5.5, 13.5);
      controls.target.set(-0.5, 0, 0);
    }
    controls.update();
  };

  renderFrame = (timeMs) => {
    if (state.data) {
      particleMeshes.forEach((mesh) => {
        const request = mesh.userData.request;
        const progress = requestProgress(request, timeMs);
        mesh.visible = progress != null;
        if (progress == null) return;
        const point = state.mode === "logical"
          ? logicalPoint(request, progress, nodePositions)
          : physicalPoint(request, progress);
        mesh.position.set(point[0], point[1], point[2]);
      });
      const frame = frameAt(timeMs);
      if (cacheMesh && frame) {
        const pressure = clamp(Number(frame.cache_pressure || 0), 0, 1);
        cacheMesh.scale.y = 0.72 + pressure * 0.7;
        cacheMesh.material.emissive = new THREE.Color(0x0f4c24);
        cacheMesh.material.emissiveIntensity = 0.12 + pressure * 0.65;
      }
    }
    controls.update();
    renderer.render(world, camera);
  };

  resize = () => {
    const rect = stage.getBoundingClientRect();
    renderer.setSize(Math.max(1, rect.width), Math.max(1, rect.height), false);
    camera.aspect = Math.max(1, rect.width) / Math.max(1, rect.height);
    camera.updateProjectionMatrix();
  };

  resize();
  state.rendererKind = "three";
}

try {
  if (!canvas || !stage) throw new Error("Replay canvas unavailable");
  await initializeThree();
} catch (error) {
  initializeCanvasFallback();
  console.warn("Replay Map is using the 2D fallback:", error);
}

const observer = new ResizeObserver(() => {
  resize();
  renderFrame(state.playheadMs);
});
observer.observe(stage);
requestAnimationFrame(animationLoop);
window.dispatchEvent(new CustomEvent("tracerator-replay-ready"));
