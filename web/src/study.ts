/**
 * The nine-view shadow study.
 *
 * One scene, one geometry, one camera, rendered nine times through scissored
 * viewports. Each render differs only in which row of the lit-state texture it
 * reads, so nine dates and hours cost one upload rather than nine scenes.
 *
 * Nothing here computes shadows. The lit state of every face at every step of
 * the sweep was computed offline by scripts/build_district.py from the horizon
 * profiles, because a shadow map at district scale under a ten degree December
 * sun is a fight with depth bias, and the horizon method is the one the numbers
 * use anyway. Drawing and measuring therefore agree by construction.
 */

import * as THREE from "three";

import { type District, FLAG_ROOF, FLAG_SIMPLIFIED } from "./bundle";

const TEX_WIDTH = 512;

/**
 * How far the frame is allowed to stay wider than the drawing.
 *
 * 0 crops the site until it covers the cell edge to edge, 1 fits the whole of
 * it and leaves the rest of the cell as paper. The cells are about twice as
 * wide as they are tall and the site is square, so fitting alone leaves the
 * drawing at half the width of its own frame, which is what the first build
 * did and it read as a smudge floating in a sheet.
 */
const FRAME_SLACK = 0.45;

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
    // A lit roof is not simply lit. How much sun it gets is the cosine of the
    // angle between its normal and the beam, which is why a flat roof under a
    // ten degree December sun reads almost as dark as one in shadow, and why
    // the nine frames differ from each other at all.
    //
    // Three stops, not two. A ramp that runs out of headroom at amber turns
    // every lit roof the same orange, and the difference between a December
    // flat roof at 0.36 and a June one at 0.93 disappears into the top of it.
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

  private cells: CellTime[] = [];
  private sunDirs: THREE.Vector3[] = [];
  private extentX = 1;
  private extentY = 1;

  constructor(canvas: HTMLCanvasElement, district: District) {
    this.district = district;
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: false,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0xe3d9c2, 1);
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
      // ZG3D rings wind inward, which backface culling reads as every roof
      // facing away from the camera. Culled, the buildings render as hollow
      // boxes with their roofs missing. The winding carries orientation
      // information this project uses elsewhere, so it is not rewritten here;
      // the drawing simply draws both faces.
      side: THREE.DoubleSide,
      uniforms: {
        uLit: { value: this.litTexture },
        uCell: { value: 0 },
        uRowsPerCell: { value: this.rowsPerCell },
        uLitSize: { value: new THREE.Vector2(TEX_WIDTH, height) },
        uSelectedPlane: { value: -1 },
        uHoverPlane: { value: -1 },
        uRoofSun: { value: new THREE.Color(0xf0bb3c) },
        uRoofPale: { value: new THREE.Color(0xfce69a) },
        uRoofShade: { value: new THREE.Color(0x8792a8) },
        uWallSun: { value: new THREE.Color(0xded4be) },
        uWallShade: { value: new THREE.Color(0xcabfa6) },
        uInk: { value: new THREE.Color(0x2a2620) },
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
        uGroundSun: { value: new THREE.Color(0xdccfb4) },
        uGroundShade: { value: new THREE.Color(0xb3a382) },
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
    // forty four degrees. Steeper than an isometric because these blocks carry
    // four times more wall area than roof area and from a low angle the facades
    // bury the planes the study is about. Shallower than the first build, which
    // was steep enough to squash a square site into something nearly as tall as
    // it was wide inside a cell twice as wide as it was tall.
    const dir = new THREE.Vector3(0.72, -0.72, 0.98).normalize();
    this.camera.position.copy(dir.clone().multiplyScalar(box.getSize(new THREE.Vector3()).length()));
    this.camera.up.set(0, 0, 1);
    this.camera.lookAt(0, 0, 0);
    this.camera.updateMatrixWorld();

    // Exact projected half-extents, taken by putting every vertex through the
    // camera's own axes. The obvious closed form for this assumes a thirty
    // degree isometric; this camera is not one, and the formula came out a
    // quarter too large, which is a quarter of the cell spent on nothing.
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
    this.cells = cells;
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
      const base = (date * steps + step) * words;
      const rowOffset = c * this.rowsPerCell * TEX_WIDTH;
      for (let p = 0; p < planes; p++) {
        const bit = (d.litStates[base + (p >> 3)] >> (p & 7)) & 1;
        if (bit) this.litData[rowOffset + p] = 255;
      }

      const gBase = (date * steps + step) * ground.bytes_per_state;
      const gOffset = c * groundCells;
      for (let i = 0; i < groundCells; i++) {
        const bit = (d.groundStates[gBase + (i >> 3)] >> (i & 7)) & 1;
        if (bit) this.groundData[gOffset + i] = 255;
      }
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

  /**
   * Draw the nine views into the rects the nine cell elements actually occupy.
   *
   * The rects come from the DOM rather than from dividing the grid into thirds,
   * because the grid also carries a header row and a row-label column, and
   * assuming it does not puts every drawing in the wrong place.
   */
  render(cells: DOMRect[]) {
    const width = window.innerWidth;
    const height = window.innerHeight;
    this.renderer.setSize(width, height, false);

    for (let c = 0; c < cells.length && c < CELLS; c++) {
      const r = cells[c];
      if (r.width <= 0 || r.height <= 0) continue;
      if (r.bottom < 0 || r.top > height) continue; // scrolled out of view

      // These are CSS pixels. three.js applies the device pixel ratio itself,
      // and doing it here as well quietly puts every drawing somewhere else.
      const x = r.left;
      const y = height - r.bottom;
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

  get cellTimes(): CellTime[] {
    return this.cells;
  }

  static isSimplified(d: District, plane: number): boolean {
    return (d.planeFlags[plane] & FLAG_SIMPLIFIED) !== 0;
  }
}
