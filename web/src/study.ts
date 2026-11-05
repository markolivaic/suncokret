/**
 * The nine-view shadow study.
 *
 * One scene, one geometry, one camera, rendered nine times through scissored
 * viewports. Each render differs only in which row of the lit-state texture it
 * reads, so nine dates and hours cost one upload rather than nine scenes.
 *
 * No shadows are computed here. The lit state of every face at every step of
 * the sweep comes from scripts/build_district.py, from the same horizon
 * profiles the numbers use, so the drawing and the figures agree.
 */

import * as THREE from "three";

import { type District, FLAG_ROOF } from "./bundle";

const TEX_WIDTH = 512;

/**
 * How far the frame is allowed to stay wider than the drawing.
 *
 * 0 crops the site until it covers the cell edge to edge, 1 fits the whole of
 * it and leaves the rest of the cell as paper. The cells are about twice as
 * wide as they are tall and the site is square, so fitting alone would leave
 * the drawing at half the width of its frame.
 */
const FRAME_SLACK = 0.45;

/**
 * A CSS colour as shader input, left in sRGB. The shaders mix and output sRGB
 * directly, the way the key's swatches and gradients do, so a colour in the
 * drawing is the same colour in the key.
 */
function srgb(hex: number): THREE.Vector3 {
  return new THREE.Vector3(((hex >> 16) & 255) / 255, ((hex >> 8) & 255) / 255, (hex & 255) / 255);
}

/** The paper inside each drawing's frame, the same as --cell in the stylesheet. */
const CELL_PAPER = 0xe3d9c2;

/** Nine views stacked in one texture, so one upload covers the whole grid. */
const CELLS = 9;

export interface CellTime {
  date: number;
  step: number;
  /** Degrees above the horizon. */
  elevation: number;
  /** Compass bearing of the sun. */
  azimuth: number;
}

const VERTEX = /* glsl */ `
precision highp float;
attribute float aPlane;
attribute float aFlags;
varying float vPlane;
varying float vFlags;
varying vec3 vWorld;

void main() {
  vPlane = aPlane;
  vFlags = aFlags;
  vec4 world = modelMatrix * vec4(position, 1.0);
  vWorld = world.xyz;
  gl_Position = projectionMatrix * viewMatrix * world;
}
`;

const FRAGMENT = /* glsl */ `
// Derivatives are core in the shading language three.js compiles to here,
// so no extension directive: one would have to precede every other token
// and three.js prepends its own preamble.
precision highp float;

uniform sampler2D uLit;
uniform float uCell;
uniform float uRowsPerCell;
uniform vec2 uLitSize;
uniform float uSelectedPlane;
uniform float uHoverPlane;

uniform vec3 uRoofSun;
uniform vec3 uRoofPale;
uniform vec3 uRoofShade;
uniform vec3 uWallSun;
uniform vec3 uWallShade;
uniform vec3 uInk;
uniform vec3 uSunDir;

varying float vPlane;
varying float vFlags;
varying vec3 vWorld;

bool hasFlag(float flags, float bit) {
  return mod(floor(flags / bit), 2.0) >= 0.5;
}

void main() {
  float row = floor(vPlane / uLitSize.x) + uCell * uRowsPerCell;
  float col = mod(vPlane, uLitSize.x);
  float lit = texture2D(uLit, vec2((col + 0.5) / uLitSize.x,
                                   (row + 0.5) / uLitSize.y)).r;

  // Flat shading from the geometry itself. The winding is inward and the
  // material is double sided, so the sign is not trustworthy; roofs face up by
  // definition, which settles it.
  vec3 n = normalize(cross(dFdx(vWorld), dFdy(vWorld)));
  if (n.z < 0.0) n = -n;

  bool isRoof = hasFlag(vFlags, 1.0);
  vec3 base;
  if (isRoof) {
    // A lit roof is shaded by the cosine of the angle between its normal and
    // the beam, from shadow blue through amber to pale gold, so a December flat
    // roof (cosine 0.36) and a June one (0.93) look different.
    float cosInc = max(dot(n, uSunDir), 0.0);
    vec3 sunlit = mix(uRoofSun, uRoofPale, smoothstep(0.45, 0.95, cosInc));
    sunlit = mix(uRoofShade, sunlit, smoothstep(0.0, 0.55, cosInc));
    base = mix(uRoofShade, sunlit, step(0.5, lit));
  } else {
    base = mix(uWallShade, uWallSun, step(0.5, lit));
    base *= 0.92 + 0.08 * abs(n.z);
  }

  // Buildings the 2008 capture gave a single roof plane are hatched, the same
  // convention the carpet uses, so the reader learns it once.
  if (hasFlag(vFlags, 4.0)) {
    float stripe = sin((gl_FragCoord.x + gl_FragCoord.y) * 0.6);
    base = mix(base, base * 0.62, smoothstep(0.25, 0.85, stripe));
  }

  if (abs(vPlane - uSelectedPlane) < 0.5) {
    base = mix(base, uInk, 0.30);
  } else if (abs(vPlane - uHoverPlane) < 0.5) {
    base = mix(base, uInk, 0.13);
  }

  gl_FragColor = vec4(base, 1.0);
}
`;

const GROUND_VERTEX = /* glsl */ `
precision highp float;
varying vec2 vSite;

void main() {
  // Object space, which is metres east and north of the district centre. The
  // group gets moved to centre the drawing and this has to keep pointing at
  // the same square of city when it does.
  vSite = position.xy;
  gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(position, 1.0);
}
`;

const GROUND_FRAGMENT = /* glsl */ `
precision highp float;

uniform sampler2D uGround;
uniform float uCell;
uniform vec2 uOrigin;
uniform float uSize;
uniform float uHalfTexel;
uniform vec3 uGroundSun;
uniform vec3 uGroundShade;

varying vec2 vSite;

void main() {
  vec2 uv = (vSite - uOrigin) / uSize;

  // Nine cells stacked in one texture. Clamping half a texel inside the block
  // keeps the filter from reading the next cell's row along the seam.
  float u = clamp(uv.x, uHalfTexel, 1.0 - uHalfTexel);
  float v = (clamp(uv.y, uHalfTexel, 1.0 - uHalfTexel) + uCell) / ${CELLS}.0;

  float lit = texture2D(uGround, vec2(u, v)).r;
  gl_FragColor = vec4(mix(uGroundShade, uGroundSun, lit), 1.0);
}
`;

function groundGeometry(g: District["manifest"]["ground"]): THREE.BufferGeometry {
  const { x0_m: x, y0_m: y, size_m: s, z_m: z } = g;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.BufferAttribute(
      // prettier-ignore
      new Float32Array([
        x, y, z, x + s, y, z, x + s, y + s, z,
        x, y, z, x + s, y + s, z, x, y + s, z,
      ]),
      3,
    ),
  );
  return geometry;
}

/** Spread `count` packed bits from `states[base...]` into bytes of 0 or 255 at `out[offset...]`. */
export function unpackStates(
  states: Uint8Array,
  base: number,
  count: number,
  out: Uint8Array,
  offset: number,
) {
  for (let i = 0; i < count; i++) {
    out[offset + i] = (states[base + (i >> 3)] >> (i & 7)) & 1 ? 255 : 0;
  }
}

export class Study {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -2000, 4000);

  private readonly district: District;
  private readonly material: THREE.ShaderMaterial;
  private readonly mesh: THREE.Mesh;
  private readonly group = new THREE.Group();
  private readonly litTexture: THREE.DataTexture;
  private readonly litData: Uint8Array<ArrayBuffer>;
  private readonly rowsPerCell: number;
  private readonly groundMaterial: THREE.ShaderMaterial;
  private readonly groundTexture: THREE.DataTexture;
  private readonly groundData: Uint8Array<ArrayBuffer>;
  private readonly raycaster = new THREE.Raycaster();

  private sunDirs: THREE.Vector3[] = [];
  private extentX = 1;
  private extentY = 1;

  constructor(canvas: HTMLCanvasElement, district: District) {
    this.district = district;
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setScissorTest(true);

    const planes = district.planeFlags.length;
    this.rowsPerCell = Math.ceil(planes / TEX_WIDTH);
    const height = this.rowsPerCell * CELLS;
    this.litData = new Uint8Array(new ArrayBuffer(TEX_WIDTH * height));
    this.litTexture = new THREE.DataTexture(
      this.litData,
      TEX_WIDTH,
      height,
      THREE.RedFormat,
      THREE.UnsignedByteType,
    );
    this.litTexture.needsUpdate = true;

    const geometry = this.buildGeometry();
    this.material = new THREE.ShaderMaterial({
      vertexShader: VERTEX,
      fragmentShader: FRAGMENT,
      // ZG3D rings wind inward, so backface culling would drop the roofs. The
      // winding is used elsewhere for orientation, so both faces are drawn.
      side: THREE.DoubleSide,
      uniforms: {
        uLit: { value: this.litTexture },
        uCell: { value: 0 },
        uRowsPerCell: { value: this.rowsPerCell },
        uLitSize: { value: new THREE.Vector2(TEX_WIDTH, height) },
        uSelectedPlane: { value: -1 },
        uHoverPlane: { value: -1 },
        uRoofSun: { value: srgb(0xf0bb3c) },
        uRoofPale: { value: srgb(0xfce69a) },
        uRoofShade: { value: srgb(0x5b6782) },
        uWallSun: { value: srgb(0xded4be) },
        uWallShade: { value: srgb(0xcabfa6) },
        uInk: { value: srgb(0x2a2620) },
        uSunDir: { value: new THREE.Vector3(0, 0, 1) },
      },
    });

    this.mesh = new THREE.Mesh(geometry, this.material);

    const g = district.manifest.ground;
    this.groundData = new Uint8Array(new ArrayBuffer(g.cells * g.cells * CELLS));
    this.groundTexture = new THREE.DataTexture(
      this.groundData,
      g.cells,
      g.cells * CELLS,
      THREE.RedFormat,
      THREE.UnsignedByteType,
    );
    // Filtered, so a two metre cell reads as a shadow edge and not as a stair.
    this.groundTexture.minFilter = THREE.LinearFilter;
    this.groundTexture.magFilter = THREE.LinearFilter;
    this.groundTexture.needsUpdate = true;

    this.groundMaterial = new THREE.ShaderMaterial({
      vertexShader: GROUND_VERTEX,
      fragmentShader: GROUND_FRAGMENT,
      uniforms: {
        uGround: { value: this.groundTexture },
        uCell: { value: 0 },
        uOrigin: { value: new THREE.Vector2(g.x0_m, g.y0_m) },
        uSize: { value: g.size_m },
        uHalfTexel: { value: 0.5 / g.cells },
        uGroundSun: { value: srgb(0xdccfb4) },
        uGroundShade: { value: srgb(0xb3a382) },
      },
    });

    this.group.add(new THREE.Mesh(groundGeometry(g), this.groundMaterial));
    this.group.add(this.mesh);
    this.scene.add(this.group);
    this.frameCamera();
  }

  private buildGeometry(): THREE.BufferGeometry {
    const d = this.district;
    const triangles = d.planeOfTriangle.length;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      "position",
      new THREE.BufferAttribute(d.positions, 3),
    );

    // One value per vertex, constant across each triangle.
    const plane = new Float32Array(triangles * 3);
    const flags = new Float32Array(triangles * 3);
    for (let t = 0; t < triangles; t++) {
      const p = d.planeOfTriangle[t];
      const f = d.planeFlags[p];
      for (let k = 0; k < 3; k++) {
        plane[t * 3 + k] = p;
        flags[t * 3 + k] = f;
      }
    }
    geometry.setAttribute("aPlane", new THREE.BufferAttribute(plane, 1));
    geometry.setAttribute("aFlags", new THREE.BufferAttribute(flags, 1));
    geometry.computeBoundingSphere();
    return geometry;
  }

  private frameCamera() {
    const g = this.district.manifest.ground;
    const box = new THREE.Box3().setFromBufferAttribute(
      this.mesh.geometry.getAttribute("position") as THREE.BufferAttribute,
    );
    box.expandByPoint(new THREE.Vector3(g.x0_m, g.y0_m, g.z_m));
    box.expandByPoint(new THREE.Vector3(g.x0_m + g.size_m, g.y0_m + g.size_m, g.z_m));

    // The bundle keeps height above the datum, so the district sits about a
    // hundred and twenty metres up. Without this the camera looks at empty air
    // below the city.
    const centre = box.getCenter(new THREE.Vector3());
    this.group.position.set(-centre.x, -centre.y, -centre.z);

    // A plan oblique down the diagonal, north up and to the right, at about
    // 44 degrees. These blocks carry four times more wall area than roof area,
    // so a lower view would hide the roofs behind the facades.
    const dir = new THREE.Vector3(0.72, -0.72, 0.98).normalize();
    this.camera.position.copy(dir.clone().multiplyScalar(box.getSize(new THREE.Vector3()).length()));
    this.camera.up.set(0, 0, 1);
    this.camera.lookAt(0, 0, 0);
    this.camera.updateMatrixWorld();

    // Projected half-extents from every vertex through the camera's own axes;
    // a closed form would assume a 30 degree isometric, which this is not.
    const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0);
    const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 1);
    const p = this.district.positions;
    const corners = [
      g.x0_m, g.y0_m, g.z_m,
      g.x0_m + g.size_m, g.y0_m, g.z_m,
      g.x0_m + g.size_m, g.y0_m + g.size_m, g.z_m,
      g.x0_m, g.y0_m + g.size_m, g.z_m,
    ];
    let ex = 0;
    let ey = 0;
    const v = new THREE.Vector3();
    const measure = (xs: ArrayLike<number>) => {
      for (let i = 0; i < xs.length; i += 3) {
        v.set(xs[i], xs[i + 1], xs[i + 2]).add(this.group.position);
        ex = Math.max(ex, Math.abs(v.dot(right)));
        ey = Math.max(ey, Math.abs(v.dot(up)));
      }
    };
    measure(p);
    measure(corners);
    this.extentX = ex;
    this.extentY = ey;
  }

  setCells(cells: CellTime[]) {
    this.sunDirs = cells.map((c) => {
      const e = (c.elevation * Math.PI) / 180;
      const a = (c.azimuth * Math.PI) / 180;
      return new THREE.Vector3(
        Math.cos(e) * Math.sin(a),
        Math.cos(e) * Math.cos(a),
        Math.sin(e),
      );
    });
    const d = this.district;
    const planes = d.planeFlags.length;
    const steps = d.manifest.sweep.steps;
    const words = d.manifest.sweep.bytes_per_state;
    const ground = d.manifest.ground;
    const groundCells = ground.cells * ground.cells;
    this.litData.fill(0);
    this.groundData.fill(0);

    for (let c = 0; c < cells.length && c < CELLS; c++) {
      const { date, step } = cells[c];
      unpackStates(
        d.litStates,
        (date * steps + step) * words,
        planes,
        this.litData,
        c * this.rowsPerCell * TEX_WIDTH,
      );
      unpackStates(
        d.groundStates,
        (date * steps + step) * ground.bytes_per_state,
        groundCells,
        this.groundData,
        c * groundCells,
      );
    }
    this.litTexture.needsUpdate = true;
    this.groundTexture.needsUpdate = true;
  }

  setSelected(plane: number) {
    this.material.uniforms.uSelectedPlane.value = plane;
  }

  setHover(plane: number) {
    this.material.uniforms.uHoverPlane.value = plane;
  }

  private frustumFor(width: number, height: number) {
    // Fit the site's own projected extents. A bounding sphere would spend most
    // of a square site's frame on empty corners.
    //
    // Then most of the way from fitting toward filling. A square site in a cell
    // twice as wide as it is tall cannot do both: fitting leaves half the cell
    // empty, filling cuts the ends off the site. The ground carries on past the
    // crop, so what leaves the frame is paving rather than a building.
    const aspect = width / height;
    const contain = Math.max(this.extentX, this.extentY * aspect);
    const cover = Math.min(this.extentX, this.extentY * aspect);
    const halfW = cover + (contain - cover) * FRAME_SLACK;
    this.camera.left = -halfW;
    this.camera.right = halfW;
    this.camera.top = halfW / aspect;
    this.camera.bottom = -halfW / aspect;
    this.camera.updateProjectionMatrix();
  }

  /** Match the drawing buffer to the canvas element. Call on resize only. */
  resize() {
    const canvas = this.renderer.domElement;
    this.renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
  }

  /**
   * Draw the nine views into the rects the nine cell elements occupy, measured
   * from the DOM because the grid also holds a header row and a label column.
   */
  render(cells: DOMRect[]) {
    const frame = this.renderer.domElement.getBoundingClientRect();
    const height = frame.height;

    // Clear everything to transparent first, so a cell that has scrolled away
    // leaves nothing behind; each visible cell is then filled with its paper.
    this.renderer.setScissorTest(false);
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.clear();
    this.renderer.setScissorTest(true);
    this.renderer.setClearColor(CELL_PAPER, 1);

    for (let c = 0; c < cells.length && c < CELLS; c++) {
      const r = cells[c];
      if (r.width <= 0 || r.height <= 0) continue;
      if (r.bottom < frame.top || r.top > frame.bottom) continue;

      // CSS pixels, relative to the canvas. three.js applies the device pixel
      // ratio inside setViewport and setScissor.
      const x = r.left - frame.left;
      const y = height - (r.bottom - frame.top);
      this.renderer.setViewport(x, y, r.width, r.height);
      this.renderer.setScissor(x, y, r.width, r.height);

      this.frustumFor(r.width, r.height);
      this.material.uniforms.uCell.value = c;
      this.groundMaterial.uniforms.uCell.value = c;
      if (this.sunDirs[c]) {
        this.material.uniforms.uSunDir.value.copy(this.sunDirs[c]);
      }
      this.renderer.render(this.scene, this.camera);
    }
  }

  /** Where a plane's centre falls inside a cell of the given size, in CSS pixels. */
  locate(plane: number, width: number, height: number): { x: number; y: number } | null {
    const centre = this.planeCentre(plane);
    if (!centre) return null;
    this.frustumFor(width, height);
    const p = centre.clone().add(this.group.position).project(this.camera);
    return { x: ((p.x + 1) / 2) * width, y: ((1 - p.y) / 2) * height };
  }

  private planeCentre(plane: number): THREE.Vector3 | null {
    const pot = this.district.planeOfTriangle;
    const pos = this.district.positions;
    const sum = new THREE.Vector3();
    let n = 0;
    for (let t = 0; t < pot.length; t++) {
      if (pot[t] !== plane) continue;
      for (let k = 0; k < 3; k++) {
        const i = (3 * t + k) * 3;
        sum.x += pos[i];
        sum.y += pos[i + 1];
        sum.z += pos[i + 2];
        n++;
      }
    }
    return n ? sum.divideScalar(n) : null;
  }

  /** Which roof plane is under this pointer position, or -1. */
  pick(cells: DOMRect[], clientX: number, clientY: number): number {
    let hitCell = -1;
    for (let c = 0; c < cells.length; c++) {
      const r = cells[c];
      if (
        clientX >= r.left &&
        clientX <= r.right &&
        clientY >= r.top &&
        clientY <= r.bottom
      ) {
        hitCell = c;
        break;
      }
    }
    if (hitCell < 0) return -1;

    const r = cells[hitCell];
    this.frustumFor(r.width, r.height);

    const ndc = new THREE.Vector2(
      ((clientX - r.left) / r.width) * 2 - 1,
      -(((clientY - r.top) / r.height) * 2 - 1),
    );
    this.raycaster.setFromCamera(ndc, this.camera);
    const hits = this.raycaster.intersectObject(this.mesh, false);
    for (const hit of hits) {
      if (hit.faceIndex === undefined || hit.faceIndex === null) continue;
      const plane = this.district.planeOfTriangle[hit.faceIndex];
      const flags = this.district.planeFlags[plane];
      if ((flags & FLAG_ROOF) !== 0) return plane;
    }
    return -1;
  }
}
