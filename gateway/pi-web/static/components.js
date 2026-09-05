(function () {
  "use strict";

  class ModuLinkrElement extends HTMLElement {
    emit(nombre, detalle) {
      this.dispatchEvent(new CustomEvent(nombre, {
        bubbles: true,
        composed: true,
        detail: detalle,
      }));
    }
  }

  class ModuLinkrIcon extends HTMLElement {
    static manifestPromise = null;
    static chunks = new Map();
    static paths = new Map();
    static localPaths = new Map([
      ["radio-handheld-dual", "M9,2A1,1 0 0,0 8,3C8,8.67 8,14.33 8,20C8,21.11 8.89,22 10,22H15C16.11,22 17,21.11 17,20V9C17,7.89 16.11,7 15,7H10V3A1,1 0 0,0 9,2M10,9H15V13H10V9ZM16,2A1,1 0 0,0 15,3V9H17V3A1,1 0 0,0 16,2Z"],
    ]);

    static get observedAttributes() {
      return ["name", "label"];
    }

    connectedCallback() {
      this._renderId = 0;
      this.render();
    }

    attributeChangedCallback() {
      if (this.isConnected) this.render();
    }

    static async manifest() {
      if (!this.manifestPromise) {
        this.manifestPromise = fetch("/static/mdi/manifest.json")
          .then((respuesta) => {
            if (!respuesta.ok) throw new Error(`catálogo MDI: HTTP ${respuesta.status}`);
            return respuesta.json();
          });
      }
      return this.manifestPromise;
    }

    static async path(nombre) {
      if (this.paths.has(nombre)) return this.paths.get(nombre);
      const manifiesto = await this.manifest();
      const bloque = manifiesto.chunks.find((candidato) =>
        nombre >= candidato.first && nombre <= candidato.last);
      if (!bloque) return null;
      if (!this.chunks.has(bloque.file)) {
        this.chunks.set(bloque.file,
          fetch(`/static/mdi/${bloque.file}`).then((respuesta) => {
            if (!respuesta.ok) throw new Error(`bloque MDI: HTTP ${respuesta.status}`);
            return respuesta.json();
          }));
      }
      const iconos = await this.chunks.get(bloque.file);
      for (const [clave, camino] of Object.entries(iconos)) this.paths.set(clave, camino);
      return iconos[nombre] ?? null;
    }

    async render() {
      const renderId = ++this._renderId;
      const referencia = this.getAttribute("name") ?? "";
      const esLocal = referencia.startsWith("modulinkr:");
      const nombre = esLocal
        ? referencia.slice(10)
        : (referencia.startsWith("mdi:") ? referencia.slice(4) : "");
      const etiqueta = this.getAttribute("label");
      this.replaceChildren();
      if (etiqueta) {
        this.setAttribute("role", "img");
        this.setAttribute("aria-label", etiqueta);
        this.removeAttribute("aria-hidden");
      } else {
        this.setAttribute("aria-hidden", "true");
        this.removeAttribute("role");
        this.removeAttribute("aria-label");
      }
      if (!nombre) return;

      try {
        let camino = esLocal
          ? ModuLinkrIcon.localPaths.get(nombre) ?? null
          : await ModuLinkrIcon.path(nombre);
        if (!camino) {
          this.dataset.iconError = "";
          console.warn(`No existe el icono ${referencia}`);
          camino = await ModuLinkrIcon.path("help-circle-outline");
        } else {
          delete this.dataset.iconError;
        }
        if (renderId !== this._renderId || !camino) return;
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("focusable", "false");
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", camino);
        svg.appendChild(path);
        this.appendChild(svg);
      } catch (error) {
        this.dataset.iconError = "";
        console.error(`No se pudo cargar ${referencia}`, error);
      }
    }
  }

  class ModuLinkrSidebar extends ModuLinkrElement {
    connectedCallback() {
      this.setAttribute("aria-label", "Navegación principal");
      this._menu = this.querySelector("#btn-menu");
      this._mobileMenu = document.getElementById("btn-menu-movil");
      this._backdrop = document.getElementById("sidebar-fondo");
      this._toggle = () => this.toggle();
      this._toggleMobile = () => { this.mobileOpen = !this.mobileOpen; };
      this._closeMobile = () => { this.mobileOpen = false; };
      this._closeOnNavigation = (evento) => {
        if (evento.target.closest("a") && this._isMobile()) this.mobileOpen = false;
      };
      this._escape = (evento) => {
        if (evento.key === "Escape" && this.mobileOpen) this.mobileOpen = false;
      };
      this._resize = () => {
        if (this._isMobile()) {
          this.mobileOpen = this.mobileOpen;
        } else {
          this.mobileOpen = false;
          this.collapsed = this.collapsed;
        }
      };
      this._menu?.addEventListener("click", this._toggle);
      this._mobileMenu?.addEventListener("click", this._toggleMobile);
      this._backdrop?.addEventListener("click", this._closeMobile);
      this.addEventListener("click", this._closeOnNavigation);
      document.addEventListener("keydown", this._escape);
      window.addEventListener("resize", this._resize);
      this.collapsed = localStorage.getItem("modulinkr_sb") === "1";
      this.mobileOpen = false;
    }

    disconnectedCallback() {
      this._menu?.removeEventListener("click", this._toggle);
      this._mobileMenu?.removeEventListener("click", this._toggleMobile);
      this._backdrop?.removeEventListener("click", this._closeMobile);
      this.removeEventListener("click", this._closeOnNavigation);
      document.removeEventListener("keydown", this._escape);
      window.removeEventListener("resize", this._resize);
    }

    set collapsed(valor) {
      const activo = Boolean(valor);
      document.body.classList.toggle("sb-contraida", activo);
      if (this._menu) {
        this._menu.title = activo ? "Expandir menú" : "Contraer menú";
        this._menu.setAttribute("aria-label", activo ? "Expandir menú" : "Contraer menú");
        this._menu.setAttribute("aria-expanded", String(!activo));
      }
    }

    get collapsed() {
      return document.body.classList.contains("sb-contraida");
    }

    set mobileOpen(valor) {
      const abierto = Boolean(valor);
      const estabaAbierto = this.mobileOpen;
      const movil = this._isMobile();
      document.body.classList.toggle("sb-movil-abierta", abierto);
      if (this._backdrop) this._backdrop.hidden = !abierto;
      const contenido = document.getElementById("contenido");
      if (contenido) contenido.inert = movil && abierto;
      if (movil && !abierto) this.setAttribute("aria-hidden", "true");
      else this.removeAttribute("aria-hidden");
      if (this._mobileMenu) {
        this._mobileMenu.setAttribute("aria-expanded", String(abierto));
        this._mobileMenu.title = abierto ? "Cerrar menú" : "Abrir menú";
        this._mobileMenu.setAttribute("aria-label", abierto ? "Cerrar menú" : "Abrir menú");
      }
      if (this._menu && this._isMobile()) {
        this._menu.title = "Cerrar menú";
        this._menu.setAttribute("aria-label", "Cerrar menú");
        this._menu.setAttribute("aria-expanded", String(abierto));
      }
      if (movil && abierto && !estabaAbierto) {
        requestAnimationFrame(() => this._menu?.focus());
      } else if (movil && !abierto && estabaAbierto && !this._mobileMenu?.hidden) {
        requestAnimationFrame(() => this._mobileMenu?.focus());
      }
    }

    get mobileOpen() {
      return document.body.classList.contains("sb-movil-abierta");
    }

    _isMobile() {
      return window.matchMedia("(max-width: 860px)").matches;
    }

    toggle() {
      if (this._isMobile()) {
        this.mobileOpen = !this.mobileOpen;
        return;
      }
      this.collapsed = !this.collapsed;
      localStorage.setItem("modulinkr_sb", this.collapsed ? "1" : "0");
    }

    set activeView(vista) {
      this.querySelectorAll(".nav-item[data-view]").forEach((enlace) => {
        const activo = enlace.dataset.view === vista;
        enlace.classList.toggle("active", activo);
        if (activo) enlace.setAttribute("aria-current", "page");
        else enlace.removeAttribute("aria-current");
      });
    }
  }

  class ModuLinkrAppHeader extends ModuLinkrElement {
    connectedCallback() {
      this.setAttribute("role", "banner");
    }

    set title(valor) {
      const titulo = this.querySelector("#titulo-vista");
      if (titulo) titulo.textContent = valor;
    }

    setNetworkStatus(online, total) {
      const badge = this.querySelector("#badge-red");
      if (!badge || !total || online === total) {
        if (badge) badge.hidden = true;
        return;
      }
      const sinConexion = Math.max(0, total - online);
      badge.textContent = `${sinConexion} ${sinConexion === 1
        ? "nodo sin conexión" : "nodos sin conexión"}`;
      badge.className = "badge " + (online === 0 ? "offline" : "warn");
      badge.hidden = false;
    }
  }

  class ModuLinkrApp extends ModuLinkrElement {
    connectedCallback() {
      this.setAttribute("data-component", "app-shell");
    }
  }

  class ModuLinkrViewRouter extends ModuLinkrElement {
    connectedCallback() {
      this.setAttribute("role", "main");
    }

    show(vista) {
      this.querySelectorAll(":scope > modulinkr-view").forEach((panel) => {
        panel.hidden = panel.dataset.view !== vista;
      });
    }
  }

  class ModuLinkrView extends ModuLinkrElement {
    connectedCallback() {
      this.setAttribute("role", "region");
      if (!this.hasAttribute("aria-label")) {
        this.setAttribute("aria-label", this.dataset.view || "Vista");
      }
    }
  }

  class ModuLinkrNodeCard extends ModuLinkrElement {
    connectedCallback() {
      this.setAttribute("role", "group");
      this.setAttribute("aria-label", this._label());
      this._header = this.querySelector(".tn-cabecera");
      this._header?.setAttribute("role", "button");
      this._header?.setAttribute("tabindex", "0");
      this._header?.setAttribute("aria-label", this._label());
      this.addEventListener("click", this._onClick);
      this.addEventListener("keydown", this._onKeydown);
    }

    disconnectedCallback() {
      this.removeEventListener("click", this._onClick);
      this.removeEventListener("keydown", this._onKeydown);
    }

    _label() {
      const nombre = this.querySelector(".tn-nombre")?.textContent?.trim();
      return nombre ? `Abrir ${nombre}` : "Abrir nodo";
    }

    _onClick = (evento) => {
      const medida = evento.target.closest("modulinkr-measurement");
      if (medida && this.contains(medida)) {
        evento.stopPropagation();
        this.emit("modulinkr-measurement-open", {
          origin: Number(medida.dataset.origin),
          channel: Number(medida.dataset.canal),
        });
        return;
      }
      const interactivo = evento.target.closest("button, a, input, select, textarea");
      if (interactivo && this.contains(interactivo)) return;
      this.emit("modulinkr-node-open", {
        origin: Number(this.dataset.origin),
      });
    };

    _onKeydown = (evento) => {
      if (evento.target !== this._header) return;
      if (evento.key === "Enter" || evento.key === " ") {
        evento.preventDefault();
        this.emit("modulinkr-node-open", {
          origin: Number(this.dataset.origin),
        });
      }
    };
  }

  class ModuLinkrMeasurement extends ModuLinkrElement {
    connectedCallback() {
      this.setAttribute("role", "button");
      this.setAttribute("tabindex", "0");
      const nombre = this.querySelector(".s-nombre")?.textContent?.trim();
      this.setAttribute("aria-label", nombre
        ? `Abrir histórico de ${nombre}` : "Abrir histórico");
      this.addEventListener("keydown", this._onKeydown);
    }

    disconnectedCallback() {
      this.removeEventListener("keydown", this._onKeydown);
    }

    _onKeydown = (evento) => {
      if (evento.key === "Enter" || evento.key === " ") {
        evento.preventDefault();
        this.click();
      }
    };
  }

  class ModuLinkrMeasurePicker extends ModuLinkrElement {
    connectedCallback() {
      if (this._iniciado) return;
      this._iniciado = true;
      this._catalogo = this._catalogo ?? [];
      this._seleccion = this._seleccion ?? new Set();
      this._aplicada = this._aplicada ?? new Set();
      this._modo = this._modo ?? "nodo";
      this._modoAplicado = this._modoAplicado ?? this._modo;
      this._soloSeleccionadas = false;
      this._gruposAbiertos = new Set();
      this._catalogoContenedor = this.querySelector(".medidas-catalogo");
      this._buscar = this.querySelector(".medidas-buscar");
      this._cuentas = this.querySelectorAll(".medidas-cuenta, .medidas-cuenta-texto");
      this._solo = this.querySelector(".medidas-solo");
      this._limpiar = this.querySelector(".medidas-limpiar");
      this._cerrar = this.querySelector(".medidas-panel-cerrar");
      this._fondo = document.getElementById("medidas-panel-fondo");
      this._botonAbrir = document.getElementById("btn-medidas");
      this.setAttribute("aria-label", "Selección de medidas");

      this._buscar?.addEventListener("input", this._renderizar);
      this.querySelectorAll(".modo-btn").forEach((boton) =>
        boton.addEventListener("click", this._cambiarModo));
      this._catalogoContenedor?.addEventListener("click", this._accionCatalogo);
      this._solo?.addEventListener("click", this._alternarSoloSeleccionadas);
      this._limpiar?.addEventListener("click", this._limpiarSeleccion);
      this._cerrar?.addEventListener("click", this._cerrarPanel);
      this._fondo?.addEventListener("click", this._cerrarPanel);
      document.addEventListener("keydown", this._tecladoDocumento);
      window.addEventListener("resize", this._ajustarVentana);
      window.addEventListener("hashchange", this._cerrarAlNavegar);
      this._renderizar();
    }

    disconnectedCallback() {
      this._buscar?.removeEventListener("input", this._renderizar);
      this.querySelectorAll(".modo-btn").forEach((boton) =>
        boton.removeEventListener("click", this._cambiarModo));
      this._catalogoContenedor?.removeEventListener("click", this._accionCatalogo);
      this._solo?.removeEventListener("click", this._alternarSoloSeleccionadas);
      this._limpiar?.removeEventListener("click", this._limpiarSeleccion);
      this._cerrar?.removeEventListener("click", this._cerrarPanel);
      this._fondo?.removeEventListener("click", this._cerrarPanel);
      document.removeEventListener("keydown", this._tecladoDocumento);
      window.removeEventListener("resize", this._ajustarVentana);
      window.removeEventListener("hashchange", this._cerrarAlNavegar);
    }

    set catalog(valor) {
      this._catalogo = Array.isArray(valor) ? valor : [];
      this._mapaCanales = new Map();
      for (const nodo of this._catalogo) {
        for (const canal of nodo.channels ?? []) {
          this._mapaCanales.set(String(canal.channel_id), canal.channel_id);
        }
      }
      this._gruposAbiertos?.clear();
      if (this._iniciado) this._renderizar();
    }

    set value(valor) {
      const seleccion = Array.isArray(valor?.selection) ? valor.selection : [];
      this._seleccion = new Set(seleccion.map(String));
      this._aplicada = new Set(this._seleccion);
      this._modo = valor?.mode === "medida" ? "medida" : "nodo";
      this._modoAplicado = this._modo;
      this._gruposAbiertos?.clear();
      for (const grupo of this._grupos()) {
        if (grupo.canales.some((canal) => this._seleccion.has(canal.clave))) {
          this._gruposAbiertos.add(grupo.clave);
        }
      }
      if (this._iniciado) this._renderizar();
    }

    get value() {
      return {
        selection: this._valoresSeleccionados(this._aplicada),
        mode: this._modoAplicado,
      };
    }

    set error(mensaje) {
      if (!this._catalogoContenedor) return;
      this._catalogoContenedor.replaceChildren();
      const aviso = document.createElement("p");
      aviso.className = "aviso error";
      aviso.textContent = mensaje;
      this._catalogoContenedor.appendChild(aviso);
    }

    open() {
      if (!window.matchMedia("(max-width: 1100px)").matches) return;
      this._renderizar();
      this.classList.add("abierto");
      this.setAttribute("role", "dialog");
      this.setAttribute("aria-modal", "true");
      document.body.classList.add("datos-panel-abierto");
      if (this._fondo) this._fondo.hidden = false;
      this._botonAbrir?.setAttribute("aria-expanded", "true");
      requestAnimationFrame(() => this._buscar?.focus());
    }

    close() {
      this.classList.remove("abierto");
      this.removeAttribute("role");
      this.removeAttribute("aria-modal");
      document.body.classList.remove("datos-panel-abierto");
      if (this._fondo) this._fondo.hidden = true;
      this._botonAbrir?.setAttribute("aria-expanded", "false");
      if (window.matchMedia("(max-width: 1100px)").matches) {
        requestAnimationFrame(() => this._botonAbrir?.focus());
      }
    }

    _valoresSeleccionados(conjunto) {
      return [...conjunto].map((clave) => this._mapaCanales?.get(clave) ?? clave);
    }

    _nombreMedida(canal) {
      if (canal.name) return canal.name;
      const nombre = String(canal.read_id ?? "Medida").replace(/[_-]+/g, " ");
      return nombre.charAt(0).toUpperCase() + nombre.slice(1);
    }

    _iconoMedida(canal) {
      const texto = `${canal.read_id ?? ""} ${canal.name ?? ""}`.toLowerCase();
      if (/temp|° ?c/.test(texto)) return "thermometer";
      if (/hum|rh|moist/.test(texto)) return "water-percent";
      if (/bat/.test(texto)) return "battery";
      if (/pressure|presion|presión/.test(texto)) return "gauge";
      if (/level|nivel|tank|deposit|depósito/.test(texto)) return "storage-tank";
      if (/volt|power|watt/.test(texto)) return "lightning-bolt";
      return "pulse";
    }

    _grupos() {
      if (this._modo === "nodo") {
        return this._catalogo.map((nodo) => ({
          clave: `n${nodo.node_id}`,
          titulo: nodo.name ?? `Nodo ${nodo.node_id}`,
          detalle: `Nodo ${nodo.node_id}`,
          canales: (nodo.channels ?? []).map((canal) => ({
            clave: String(canal.channel_id),
            texto: this._nombreMedida(canal),
            detalle: canal.unit ?? "",
            icono: this._iconoMedida(canal),
          })),
        }));
      }

      const grupos = new Map();
      for (const nodo of this._catalogo) {
        for (const canal of nodo.channels ?? []) {
          const claveMedida = `${canal.read_id ?? canal.name}|${canal.unit ?? ""}`;
          if (!grupos.has(claveMedida)) {
            grupos.set(claveMedida, {
              clave: `m${claveMedida}`,
              titulo: this._nombreMedida(canal),
              detalle: canal.unit ?? "",
              canales: [],
            });
          }
          grupos.get(claveMedida).canales.push({
            clave: String(canal.channel_id),
            texto: nodo.name ?? `Nodo ${nodo.node_id}`,
            detalle: `Nodo ${nodo.node_id}`,
            icono: nodo.node_type === "super_node"
              ? "modulinkr:radio-handheld-dual" : "radio-handheld",
          });
        }
      }
      return [...grupos.values()];
    }

    _crearIcono(nombre) {
      const icono = document.createElement("modulinkr-icon");
      icono.setAttribute("name", nombre.includes(":") ? nombre : `mdi:${nombre}`);
      return icono;
    }

    _crearGrupo(grupo, canales, abrir) {
      const elemento = document.createElement("section");
      elemento.className = "medidas-grupo";
      elemento.dataset.grupo = grupo.clave;

      const cabecera = document.createElement("div");
      cabecera.className = "medidas-grupo-cabecera";
      const alternar = document.createElement("button");
      alternar.type = "button";
      alternar.className = "medidas-grupo-toggle";
      alternar.dataset.accion = "alternar";
      alternar.dataset.grupo = grupo.clave;
      alternar.setAttribute("aria-expanded", String(abrir));
      alternar.appendChild(this._crearIcono(abrir ? "chevron-down" : "chevron-right"));
      const textos = document.createElement("span");
      textos.className = "medidas-grupo-textos";
      const titulo = document.createElement("strong");
      titulo.textContent = grupo.titulo;
      const detalle = document.createElement("small");
      const marcadas = grupo.canales.filter((canal) => this._seleccion.has(canal.clave)).length;
      detalle.textContent = grupo.detalle
        ? `${grupo.detalle} · ${marcadas}/${grupo.canales.length}`
        : `${marcadas}/${grupo.canales.length}`;
      textos.append(titulo, detalle);
      alternar.appendChild(textos);

      const todas = document.createElement("button");
      todas.type = "button";
      todas.className = "medidas-grupo-todas";
      todas.dataset.accion = "todas";
      todas.dataset.grupo = grupo.clave;
      todas.textContent = marcadas === grupo.canales.length && grupo.canales.length
        ? "Quitar todas" : "Seleccionar todas";
      cabecera.append(alternar, todas);
      elemento.appendChild(cabecera);

      const cuerpo = document.createElement("div");
      cuerpo.className = "medidas-grupo-cuerpo";
      cuerpo.hidden = !abrir;
      const arbol = document.createElement("wa-tree");
      arbol.setAttribute("selection", "multiple");
      arbol.setAttribute("aria-label", grupo.titulo);
      for (const canal of canales) {
        const opcion = document.createElement("wa-tree-item");
        opcion.dataset.canal = canal.clave;
        opcion.selected = this._seleccion.has(canal.clave);
        const contenido = document.createElement("span");
        contenido.className = "medida-opcion";
        contenido.appendChild(this._crearIcono(canal.icono));
        const nombre = document.createElement("span");
        nombre.className = "medida-opcion-nombre";
        nombre.textContent = canal.texto;
        const secundario = document.createElement("span");
        secundario.className = "medida-opcion-detalle";
        secundario.textContent = canal.detalle;
        contenido.append(nombre, secundario);
        opcion.appendChild(contenido);
        arbol.appendChild(opcion);
      }
      arbol.addEventListener("wa-selection-change", this._cambioArbol);
      cuerpo.appendChild(arbol);
      elemento.appendChild(cuerpo);
      return elemento;
    }

    _renderizar = () => {
      if (!this._catalogoContenedor) return;
      this.querySelectorAll(".modo-btn").forEach((boton) => {
        const activo = boton.dataset.modo === this._modo;
        boton.classList.toggle("active", activo);
        boton.setAttribute("aria-pressed", String(activo));
      });

      const consulta = this._buscar?.value.trim().toLocaleLowerCase("es") ?? "";
      const grupos = this._grupos();
      this._gruposActuales = new Map(grupos.map((grupo) => [grupo.clave, grupo]));
      this._catalogoContenedor.replaceChildren();

      for (const grupo of grupos) {
        const coincideGrupo = `${grupo.titulo} ${grupo.detalle}`
          .toLocaleLowerCase("es").includes(consulta);
        const canales = grupo.canales.filter((canal) => {
          if (this._soloSeleccionadas && !this._seleccion.has(canal.clave)) return false;
          if (!consulta || coincideGrupo) return true;
          return `${canal.texto} ${canal.detalle}`.toLocaleLowerCase("es").includes(consulta);
        });
        if (!canales.length) continue;
        const abrir = Boolean(consulta) || this._soloSeleccionadas
          || this._gruposAbiertos.has(grupo.clave);
        this._catalogoContenedor.appendChild(this._crearGrupo(grupo, canales, abrir));
      }

      if (!this._catalogoContenedor.children.length) {
        const aviso = document.createElement("p");
        aviso.className = "medidas-vacio";
        aviso.textContent = this._soloSeleccionadas
          ? "No hay medidas seleccionadas."
          : "No hay medidas que coincidan con la búsqueda.";
        this._catalogoContenedor.appendChild(aviso);
      }
      this._actualizarResumen();
    };

    _actualizarResumen() {
      const cantidad = this._seleccion.size;
      this._cuentas?.forEach((elemento) => {
        elemento.textContent = elemento.classList.contains("medidas-cuenta-texto")
          ? `${cantidad} ${cantidad === 1 ? "seleccionada" : "seleccionadas"}`
          : String(cantidad);
      });
      if (this._limpiar) this._limpiar.disabled = cantidad === 0;
      if (this._solo) {
        this._solo.classList.toggle("active", this._soloSeleccionadas);
        this._solo.setAttribute("aria-pressed", String(this._soloSeleccionadas));
      }
    }

    _cambiarModo = (evento) => {
      this._modo = evento.currentTarget.dataset.modo === "medida" ? "medida" : "nodo";
      this._gruposAbiertos.clear();
      this._renderizar();
      this._publicarSeleccion();
    };

    _accionCatalogo = (evento) => {
      const boton = evento.target.closest("button[data-accion]");
      if (!boton || !this._catalogoContenedor.contains(boton)) return;
      const grupo = this._gruposActuales?.get(boton.dataset.grupo);
      if (!grupo) return;
      if (boton.dataset.accion === "alternar") {
        if (this._gruposAbiertos.has(grupo.clave)) this._gruposAbiertos.delete(grupo.clave);
        else this._gruposAbiertos.add(grupo.clave);
      } else if (boton.dataset.accion === "todas") {
        const todas = grupo.canales.every((canal) => this._seleccion.has(canal.clave));
        for (const canal of grupo.canales) {
          if (todas) this._seleccion.delete(canal.clave);
          else this._seleccion.add(canal.clave);
        }
      }
      this._renderizar();
      if (boton.dataset.accion === "todas") this._publicarSeleccion();
    };

    _cambioArbol = (evento) => {
      const arbol = evento.currentTarget;
      requestAnimationFrame(() => {
        arbol.querySelectorAll("wa-tree-item[data-canal]").forEach((opcion) => {
          if (opcion.selected) this._seleccion.add(opcion.dataset.canal);
          else this._seleccion.delete(opcion.dataset.canal);
        });
        this._renderizar();
        this._publicarSeleccion();
      });
    };

    _alternarSoloSeleccionadas = () => {
      this._soloSeleccionadas = !this._soloSeleccionadas;
      this._renderizar();
    };

    _limpiarSeleccion = () => {
      this._seleccion.clear();
      this._renderizar();
      this._publicarSeleccion();
    };

    _publicarSeleccion() {
      this._aplicada = new Set(this._seleccion);
      this._modoAplicado = this._modo;
      this.emit("modulinkr-measures-apply", {
        selection: this._valoresSeleccionados(this._aplicada),
        mode: this._modoAplicado,
      });
    }

    _cerrarPanel = () => this.close();

    _ajustarVentana = () => {
      if (!window.matchMedia("(max-width: 1100px)").matches
          && this.classList.contains("abierto")) this.close();
    };

    _cerrarAlNavegar = () => {
      if (this.classList.contains("abierto")) this.close();
    };

    _tecladoDocumento = (evento) => {
      if (!this.classList.contains("abierto")) return;
      if (evento.key === "Escape") {
        evento.preventDefault();
        this.close();
        return;
      }
      if (evento.key !== "Tab") return;
      const enfocables = [...this.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), wa-tree-item:not([disabled]), select:not([disabled])'
      )].filter((elemento) => !elemento.closest("[hidden]"));
      if (!enfocables.length) return;
      const primero = enfocables[0];
      const ultimo = enfocables[enfocables.length - 1];
      if (evento.shiftKey && document.activeElement === primero) {
        evento.preventDefault();
        ultimo.focus();
      } else if (!evento.shiftKey && document.activeElement === ultimo) {
        evento.preventDefault();
        primero.focus();
      }
    };
  }

  class ModuLinkrChartLegend extends ModuLinkrElement {
    connectedCallback() {
      if (this._iniciado) return;
      this._iniciado = true;
      this._elementos = this._elementos ?? [];
      this._expandida = false;
      this.addEventListener("click", this._accion);
      this._renderizar();
    }

    disconnectedCallback() {
      this.removeEventListener("click", this._accion);
    }

    set items(valor) {
      this._elementos = Array.isArray(valor) ? valor : [];
      if (this._elementos.length <= 6) this._expandida = false;
      if (this._iniciado) this._renderizar();
    }

    get items() {
      return this._elementos ?? [];
    }

    _crearEntrada(elemento) {
      const boton = document.createElement("button");
      boton.type = "button";
      boton.className = "grafico-leyenda-entrada";
      boton.dataset.accion = "serie";
      boton.dataset.serie = String(elemento.id);
      boton.setAttribute("aria-pressed", String(elemento.visible !== false));
      boton.title = `${elemento.nodo}: ${elemento.medida}`;

      const muestra = document.createElement("span");
      muestra.className = "grafico-leyenda-muestra";
      muestra.style.setProperty("--serie-color", elemento.color);
      const nombre = document.createElement("span");
      nombre.className = "grafico-leyenda-medida";
      nombre.textContent = elemento.medida;
      boton.append(muestra, nombre);
      if (elemento.unidad) {
        const unidad = document.createElement("span");
        unidad.className = "grafico-leyenda-unidad";
        unidad.textContent = elemento.unidad;
        boton.appendChild(unidad);
      }
      return boton;
    }

    _renderizar() {
      const elementos = this._elementos ?? [];
      this.replaceChildren();
      this.hidden = elementos.length === 0;
      if (!elementos.length) return;

      const resumen = document.createElement("button");
      resumen.type = "button";
      resumen.className = "grafico-leyenda-resumen";
      resumen.dataset.accion = "compacta";
      resumen.setAttribute("aria-expanded", String(this._expandida));
      resumen.textContent = `${elementos.length} ${elementos.length === 1 ? "medida" : "medidas"}`;
      const icono = document.createElement("modulinkr-icon");
      icono.setAttribute("name", this._expandida ? "mdi:chevron-up" : "mdi:chevron-down");
      resumen.appendChild(icono);
      this.appendChild(resumen);

      const cuerpo = document.createElement("div");
      cuerpo.className = "grafico-leyenda-cuerpo";
      cuerpo.classList.toggle("expandida", this._expandida);
      const visibles = this._expandida ? elementos : elementos.slice(0, 6);
      const grupos = new Map();
      for (const elemento of visibles) {
        if (!grupos.has(elemento.nodo)) grupos.set(elemento.nodo, []);
        grupos.get(elemento.nodo).push(elemento);
      }
      for (const [nodo, entradas] of grupos) {
        const grupo = document.createElement("section");
        grupo.className = "grafico-leyenda-grupo";
        const titulo = document.createElement("span");
        titulo.className = "grafico-leyenda-nodo";
        titulo.textContent = nodo;
        const lista = document.createElement("div");
        lista.className = "grafico-leyenda-lista";
        entradas.forEach((entrada) => lista.appendChild(this._crearEntrada(entrada)));
        grupo.append(titulo, lista);
        cuerpo.appendChild(grupo);
      }

      const restantes = elementos.length - visibles.length;
      if (restantes > 0) {
        const mas = document.createElement("button");
        mas.type = "button";
        mas.className = "grafico-leyenda-mas";
        mas.dataset.accion = "expandir";
        mas.textContent = `Ver ${restantes} ${restantes === 1 ? "medida más" : "medidas más"}`;
        cuerpo.appendChild(mas);
      } else if (elementos.length > 6) {
        const menos = document.createElement("button");
        menos.type = "button";
        menos.className = "grafico-leyenda-mas";
        menos.dataset.accion = "expandir";
        menos.textContent = "Ver menos";
        cuerpo.appendChild(menos);
      }
      this.appendChild(cuerpo);
    }

    _accion = (evento) => {
      const boton = evento.target.closest("button[data-accion]");
      if (!boton || !this.contains(boton)) return;
      if (boton.dataset.accion === "serie") {
        const elemento = this._elementos.find((item) => String(item.id) === boton.dataset.serie);
        if (!elemento) return;
        this.emit("modulinkr-chart-series-toggle", {
          id: elemento.id,
          visible: elemento.visible === false,
        });
        return;
      }
      this._expandida = !this._expandida;
      this._renderizar();
    };
  }

  const PREAJUSTES_PERIODO = [
    ["now-1h", "Última hora"],
    ["now-12h", "Últimas 12 horas"],
    ["now-24h", "Últimas 24 horas"],
    ["today", "Hoy"],
    ["yesterday", "Ayer"],
    ["this-week", "Esta semana"],
    ["this-month", "Este mes"],
    ["this-quarter", "Este trimestre"],
    ["this-year", "Este año"],
    ["now-7d", "Últimos 7 días"],
    ["now-30d", "Últimos 30 días"],
  ];

  const MS_HORA = 60 * 60 * 1000;

  function periodoCopia(periodo) {
    return {
      preset: periodo.preset || "",
      mode: periodo.mode,
      start: new Date(periodo.start),
      end: new Date(periodo.end),
    };
  }

  function inicioDia(fecha) {
    return new Date(fecha.getFullYear(), fecha.getMonth(), fecha.getDate());
  }

  function finDia(fecha) {
    return new Date(fecha.getFullYear(), fecha.getMonth(), fecha.getDate(), 23, 59, 59, 999);
  }

  function sumarDias(fecha, dias) {
    return new Date(
      fecha.getFullYear(), fecha.getMonth(), fecha.getDate() + dias,
      fecha.getHours(), fecha.getMinutes(), fecha.getSeconds(), fecha.getMilliseconds()
    );
  }

  function inicioSemana(fecha) {
    const dia = fecha.getDay() || 7;
    return inicioDia(sumarDias(fecha, 1 - dia));
  }

  function finMes(fecha) {
    return finDia(new Date(fecha.getFullYear(), fecha.getMonth() + 1, 0));
  }

  function isoDia(fecha) {
    const y = fecha.getFullYear();
    const m = String(fecha.getMonth() + 1).padStart(2, "0");
    const d = String(fecha.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  function horaCampo(fecha) {
    return `${String(fecha.getHours()).padStart(2, "0")}:${String(fecha.getMinutes()).padStart(2, "0")}`;
  }

  function fechaConHora(dia, hora, final = false) {
    const partesDia = String(dia).split("-").map(Number);
    const partesHora = String(hora || (final ? "23:59" : "00:00")).split(":").map(Number);
    return new Date(
      partesDia[0], partesDia[1] - 1, partesDia[2],
      partesHora[0], partesHora[1], final ? 59 : 0, final ? 999 : 0
    );
  }

  function mismoDia(a, b) {
    return a.getFullYear() === b.getFullYear()
      && a.getMonth() === b.getMonth()
      && a.getDate() === b.getDate();
  }

  function periodoDesdePreset(clave, referencia = new Date()) {
    const ahora = new Date(referencia);
    if (clave === "now-1h" || clave === "now-12h" || clave === "now-24h") {
      const horas = Number(clave.match(/\d+/)[0]);
      return { preset: clave, mode: clave, start: new Date(ahora.getTime() - horas * MS_HORA), end: ahora };
    }
    if (clave === "today") {
      return { preset: clave, mode: "day", start: inicioDia(ahora), end: ahora };
    }
    if (clave === "yesterday") {
      const ayer = sumarDias(ahora, -1);
      return { preset: clave, mode: "day", start: inicioDia(ayer), end: finDia(ayer) };
    }
    if (clave === "this-week") {
      return { preset: clave, mode: "week", start: inicioSemana(ahora), end: ahora };
    }
    if (clave === "this-month") {
      return {
        preset: clave, mode: "month",
        start: new Date(ahora.getFullYear(), ahora.getMonth(), 1), end: ahora,
      };
    }
    if (clave === "this-quarter") {
      const mes = Math.floor(ahora.getMonth() / 3) * 3;
      return { preset: clave, mode: "quarter", start: new Date(ahora.getFullYear(), mes, 1), end: ahora };
    }
    if (clave === "this-year") {
      return { preset: clave, mode: "year", start: new Date(ahora.getFullYear(), 0, 1), end: ahora };
    }
    if (clave === "now-7d" || clave === "now-30d") {
      const dias = clave === "now-7d" ? 7 : 30;
      return { preset: clave, mode: clave, start: inicioDia(sumarDias(ahora, 1 - dias)), end: ahora };
    }
    return { preset: "", mode: "custom", start: new Date(ahora.getTime() - 24 * MS_HORA), end: ahora };
  }

  class ModuLinkrPeriodSelector extends ModuLinkrElement {
    connectedCallback() {
      if (this._iniciado) return;
      this._iniciado = true;
      this._dialogo = this.querySelector(".periodo-dialogo");
      this._abrirBoton = this.querySelector(".periodo-abrir");
      this._etiqueta = this.querySelector(".periodo-etiqueta");
      this._carga = this.querySelector(".periodo-carga");
      this._ahora = this.querySelector(".periodo-ahora");
      this._ahoraMenu = this.querySelector(".periodo-ahora-menu");
      this._anterior = this.querySelector(".periodo-anterior");
      this._siguiente = this.querySelector(".periodo-siguiente");
      this._menuBoton = this.querySelector(".periodo-menu-boton");
      this._menuLista = this.querySelector(".periodo-menu-lista");
      this._exportar = this.querySelector(".periodo-exportar");
      this._preajustes = this.querySelector(".periodo-preajustes");
      this._calendario = this.querySelector("calendar-range");
      this._tituloCalendario = this.querySelector(".periodo-mes-titulo");
      this._horaInicio = this.querySelector("#periodo-hora-inicio");
      this._horaFin = this.querySelector("#periodo-hora-fin");
      this._error = this.querySelector(".periodo-error");
      this._cancelar = this.querySelector(".periodo-cancelar");
      this._seleccionar = this.querySelector(".periodo-seleccionar");
      this._periodo = periodoDesdePreset("now-24h");
      this._crearPreajustes();
      this._registrarEventos();
      this._actualizarBarra();
      customElements.whenDefined("calendar-range").then(() => this._prepararCalendario());
    }

    disconnectedCallback() {
      document.removeEventListener("pointerdown", this._cerrarMenuFuera);
      document.removeEventListener("keydown", this._teclaDocumento);
      this._observadorTitulo?.disconnect();
      clearTimeout(this._temporizadorCarga);
    }

    _crearPreajustes() {
      const fragmento = document.createDocumentFragment();
      for (const [clave, etiqueta] of PREAJUSTES_PERIODO) {
        const boton = document.createElement("button");
        boton.type = "button";
        boton.dataset.preset = clave;
        boton.textContent = etiqueta;
        fragmento.appendChild(boton);
      }
      const personalizado = document.createElement("button");
      personalizado.type = "button";
      personalizado.dataset.preset = "custom";
      personalizado.textContent = "Intervalo personalizado";
      fragmento.appendChild(personalizado);
      this._preajustes.replaceChildren(fragmento);
    }

    _registrarEventos() {
      this._abrirBoton.addEventListener("click", () => this.open());
      this._ahora.addEventListener("click", () => this._irAhora());
      this._ahoraMenu.addEventListener("click", () => {
        this._cerrarMenu();
        this._irAhora();
      });
      this._anterior.addEventListener("click", () => this._desplazar(-1));
      this._siguiente.addEventListener("click", () => this._desplazar(1));
      this._menuBoton.addEventListener("click", () => this._alternarMenu());
      this._exportar.addEventListener("click", () => {
        this._cerrarMenu();
        this.emit("modulinkr-period-export");
      });
      this._preajustes.addEventListener("click", (evento) => this._elegirPreajuste(evento));
      this._calendario.addEventListener("change", () => this._cambiarCalendario());
      this._horaInicio.addEventListener("input", () => this._cambiarHora());
      this._horaFin.addEventListener("input", () => this._cambiarHora());
      this._cancelar.addEventListener("click", () => this._dialogo.close());
      this._seleccionar.addEventListener("click", () => this._aplicarBorrador());
      this._dialogo.addEventListener("click", (evento) => {
        if (evento.target === this._dialogo) this._dialogo.close();
      });
      this._cerrarMenuFuera = (evento) => {
        if (!this.querySelector(".periodo-menu").contains(evento.target)) this._cerrarMenu();
      };
      this._teclaDocumento = (evento) => {
        if (evento.key === "Escape" && !this._menuLista.hidden) this._cerrarMenu();
      };
      document.addEventListener("pointerdown", this._cerrarMenuFuera);
      document.addEventListener("keydown", this._teclaDocumento);
    }

    _prepararCalendario() {
      this._calendario.locale = document.documentElement.lang || navigator.language || "es-ES";
      this._calendario.firstDayOfWeek = 1;
      this._calendario.showOutsideDays = true;
      this._sincronizarBorrador();
      Promise.resolve(this._calendario.updated).then(() => this._observarTituloCalendario());
    }

    _observarTituloCalendario() {
      const origen = this._calendario.shadowRoot?.querySelector("#h");
      if (!origen) return;
      const sincronizar = () => {
        this._tituloCalendario.textContent = origen.textContent.trim();
      };
      this._observadorTitulo?.disconnect();
      this._observadorTitulo = new MutationObserver(sincronizar);
      this._observadorTitulo.observe(origen, { childList: true, characterData: true, subtree: true });
      sincronizar();
    }

    open() {
      this._cerrarMenu();
      this._borrador = periodoCopia(this._periodo);
      this._ocultarError();
      this._sincronizarBorrador();
      if (!this._dialogo.open) this._dialogo.showModal();
      requestAnimationFrame(() => {
        const activo = this._preajustes.querySelector("[aria-pressed='true']");
        (activo || this._preajustes.querySelector("button"))?.focus();
      });
    }

    _elegirPreajuste(evento) {
      const boton = evento.target.closest("button[data-preset]");
      if (!boton) return;
      if (boton.dataset.preset === "custom") {
        this._borrador.preset = "";
        this._borrador.mode = "custom";
        this._sincronizarBorrador();
        customElements.whenDefined("calendar-range").then(() => this._calendario.focus({ target: "day" }));
        return;
      }
      this._borrador = periodoDesdePreset(boton.dataset.preset);
      this._sincronizarBorrador();
    }

    _cambiarCalendario() {
      if (!this._borrador) return;
      const valor = String(this._calendario.value || "");
      const [inicio, fin] = valor.split("/");
      if (!inicio || !fin) return;
      this._borrador = {
        preset: "", mode: "custom",
        start: fechaConHora(inicio, this._horaInicio.value),
        end: fechaConHora(fin, this._horaFin.value, true),
      };
      this._actualizarPreajustes();
      this._ocultarError();
    }

    _cambiarHora() {
      if (!this._borrador) return;
      const valor = String(this._calendario.value || "");
      const [inicio, fin] = valor.split("/");
      if (!inicio || !fin) return;
      this._borrador = {
        preset: "", mode: "custom",
        start: fechaConHora(inicio, this._horaInicio.value),
        end: fechaConHora(fin, this._horaFin.value, true),
      };
      this._actualizarPreajustes();
      this._ocultarError();
    }

    _sincronizarBorrador() {
      if (!this._borrador) return;
      this._horaInicio.value = horaCampo(this._borrador.start);
      this._horaFin.value = horaCampo(this._borrador.end);
      this._actualizarPreajustes();
      customElements.whenDefined("calendar-range").then(() => {
        const hoy = isoDia(new Date());
        this._calendario.max = hoy;
        this._calendario.today = hoy;
        this._calendario.value = `${isoDia(this._borrador.start)}/${isoDia(this._borrador.end)}`;
        this._calendario.focusedDate = isoDia(this._borrador.start);
      });
    }

    _actualizarPreajustes() {
      this._preajustes.querySelectorAll("button").forEach((boton) => {
        const activo = boton.dataset.preset === (this._borrador.preset || "custom");
        boton.setAttribute("aria-pressed", String(activo));
      });
    }

    _aplicarBorrador() {
      if (!this._borrador || this._borrador.end <= this._borrador.start) {
        this._mostrarError("La hora de finalización debe ser posterior a la hora de inicio.");
        this._horaFin.focus();
        return;
      }
      if (this._borrador.start > new Date()) {
        this._mostrarError("El periodo no puede comenzar en el futuro.");
        return;
      }
      if (this._borrador.end > new Date()) this._borrador.end = new Date();
      this._periodo = periodoCopia(this._borrador);
      this._dialogo.close();
      this._notificarCambio();
    }

    _mostrarError(mensaje) {
      this._error.textContent = mensaje;
      this._error.hidden = false;
    }

    _ocultarError() {
      this._error.textContent = "";
      this._error.hidden = true;
    }

    _desplazar(direccion) {
      const candidato = this._periodoDesplazado(direccion);
      if (!candidato || candidato.start > new Date()) return;
      if (candidato.end > new Date()) candidato.end = new Date();
      candidato.preset = this._presetActual(candidato);
      this._periodo = candidato;
      this._notificarCambio();
    }

    _periodoDesplazado(direccion) {
      const actual = this._periodo;
      const ahora = new Date();
      if (/^now-(1h|12h|24h)$/.test(actual.mode)) {
        const duracion = actual.end.getTime() - actual.start.getTime();
        return {
          preset: "", mode: actual.mode,
          start: new Date(actual.start.getTime() + direccion * duracion),
          end: new Date(actual.end.getTime() + direccion * duracion),
        };
      }
      if (actual.mode === "day") {
        const start = inicioDia(sumarDias(actual.start, direccion));
        const end = mismoDia(start, ahora) ? ahora : finDia(start);
        return { preset: "", mode: "day", start, end };
      }
      if (actual.mode === "week") {
        const start = inicioDia(sumarDias(actual.start, direccion * 7));
        let end = finDia(sumarDias(start, 6));
        if (start <= ahora && end > ahora) end = ahora;
        return { preset: "", mode: "week", start, end };
      }
      if (actual.mode === "month") {
        const start = new Date(actual.start.getFullYear(), actual.start.getMonth() + direccion, 1);
        let end = finMes(start);
        if (start <= ahora && end > ahora) end = ahora;
        return { preset: "", mode: "month", start, end };
      }
      if (actual.mode === "quarter") {
        const start = new Date(actual.start.getFullYear(), actual.start.getMonth() + direccion * 3, 1);
        let end = finMes(new Date(start.getFullYear(), start.getMonth() + 2, 1));
        if (start <= ahora && end > ahora) end = ahora;
        return { preset: "", mode: "quarter", start, end };
      }
      if (actual.mode === "year") {
        const start = new Date(actual.start.getFullYear() + direccion, 0, 1);
        let end = finDia(new Date(start.getFullYear(), 11, 31));
        if (start <= ahora && end > ahora) end = ahora;
        return { preset: "", mode: "year", start, end };
      }
      if (actual.mode === "now-7d" || actual.mode === "now-30d") {
        const dias = actual.mode === "now-7d" ? 7 : 30;
        return {
          preset: "", mode: actual.mode,
          start: sumarDias(actual.start, direccion * dias),
          end: sumarDias(actual.end, direccion * dias),
        };
      }
      const duracion = actual.end.getTime() - actual.start.getTime();
      return {
        preset: "", mode: "custom",
        start: new Date(actual.start.getTime() + direccion * duracion),
        end: new Date(actual.end.getTime() + direccion * duracion),
      };
    }

    _presetActual(periodo) {
      const ahora = new Date();
      if (/^now-(1h|12h|24h)$/.test(periodo.mode)
          && Math.abs(periodo.end.getTime() - ahora.getTime()) < 120000) return periodo.mode;
      if (periodo.mode === "day") {
        if (mismoDia(periodo.start, ahora)) return "today";
        if (mismoDia(periodo.start, sumarDias(ahora, -1))) return "yesterday";
      }
      if (periodo.mode === "week" && mismoDia(periodo.start, inicioSemana(ahora))) return "this-week";
      if (periodo.mode === "month"
          && periodo.start.getFullYear() === ahora.getFullYear()
          && periodo.start.getMonth() === ahora.getMonth()) return "this-month";
      if (periodo.mode === "quarter"
          && periodo.start.getFullYear() === ahora.getFullYear()
          && periodo.start.getMonth() === Math.floor(ahora.getMonth() / 3) * 3) return "this-quarter";
      if (periodo.mode === "year" && periodo.start.getFullYear() === ahora.getFullYear()) return "this-year";
      if ((periodo.mode === "now-7d" || periodo.mode === "now-30d")
          && Math.abs(periodo.end.getTime() - ahora.getTime()) < 120000) return periodo.mode;
      return "";
    }

    _irAhora() {
      const mapa = {
        day: "today", week: "this-week", month: "this-month",
        quarter: "this-quarter", year: "this-year",
      };
      const clave = mapa[this._periodo.mode] || this._periodo.mode;
      if (PREAJUSTES_PERIODO.some(([candidato]) => candidato === clave)) {
        this._periodo = periodoDesdePreset(clave);
      } else {
        const ahora = new Date();
        const duracion = this._periodo.end.getTime() - this._periodo.start.getTime();
        this._periodo = {
          preset: "", mode: "custom",
          start: new Date(ahora.getTime() - duracion), end: ahora,
        };
      }
      this._notificarCambio();
    }

    _notificarCambio() {
      this._actualizarBarra();
      this.emit("modulinkr-period-change", this.value);
    }

    _actualizarBarra() {
      const etiqueta = this._formatearPeriodo(this._periodo);
      this._etiqueta.textContent = etiqueta;
      this._abrirBoton.setAttribute("aria-label", `Seleccionar periodo. Periodo actual: ${etiqueta}`);
      const siguiente = this._periodoDesplazado(1);
      this._siguiente.disabled = !siguiente || siguiente.end > new Date();
    }

    _formatearPeriodo(periodo) {
      const etiquetaPreset = new Map(PREAJUSTES_PERIODO).get(periodo.preset);
      if (/^now-(1h|12h|24h)$/.test(periodo.preset) && etiquetaPreset) return etiquetaPreset;
      const locale = document.documentElement.lang || "es-ES";
      const fechaCorta = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short" });
      const fechaCompleta = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", year: "numeric" });
      const hora = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" });
      const anioActual = new Date().getFullYear();
      const fechaVisible = (fecha) => fecha.getFullYear() === anioActual
        ? fechaCorta.format(fecha) : fechaCompleta.format(fecha);
      if (periodo.mode === "month") {
        return new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" }).format(periodo.start);
      }
      if (periodo.mode === "year") return String(periodo.start.getFullYear());
      if (periodo.mode === "quarter") {
        const fin = new Date(periodo.start.getFullYear(), periodo.start.getMonth() + 2, 1);
        return `${fechaCorta.format(periodo.start).replace(/^\d+\s*/, "")} a ${fechaCompleta.format(fin).replace(/^\d+\s*/, "")}`;
      }
      if (mismoDia(periodo.start, periodo.end)) {
        const esDia = periodo.start.getHours() === 0 && periodo.start.getMinutes() === 0
          && (periodo.end.getHours() === 23 || mismoDia(periodo.end, new Date()));
        if (esDia) return fechaVisible(periodo.start);
        return `${fechaVisible(periodo.start)}, ${hora.format(periodo.start)} a ${hora.format(periodo.end)}`;
      }
      const mismoAnio = periodo.start.getFullYear() === periodo.end.getFullYear();
      const inicio = mismoAnio ? fechaCorta.format(periodo.start) : fechaCompleta.format(periodo.start);
      const fin = mismoAnio ? fechaVisible(periodo.end) : fechaCompleta.format(periodo.end);
      return `${inicio} a ${fin}`;
    }

    _alternarMenu() {
      const abrir = this._menuLista.hidden;
      this._menuLista.hidden = !abrir;
      this._menuBoton.setAttribute("aria-expanded", String(abrir));
      if (abrir) requestAnimationFrame(() => this._menuLista.querySelector("button:not([hidden])")?.focus());
    }

    _cerrarMenu() {
      this._menuLista.hidden = true;
      this._menuBoton.setAttribute("aria-expanded", "false");
    }

    get range() {
      return {
        desde: this._periodo.start.toISOString(),
        hasta: this._periodo.end.toISOString(),
      };
    }

    get value() {
      return {
        preset: this._periodo.preset,
        mode: this._periodo.mode,
        start: this._periodo.start.toISOString(),
        end: this._periodo.end.toISOString(),
      };
    }

    set value(valor) {
      if (!valor) return;
      if (valor.preset && PREAJUSTES_PERIODO.some(([clave]) => clave === valor.preset)) {
        this._periodo = periodoDesdePreset(valor.preset);
      } else {
        const start = new Date(valor.start);
        const end = new Date(valor.end);
        if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || end <= start) return;
        this._periodo = { preset: "", mode: valor.mode || "custom", start, end };
      }
      this._actualizarBarra();
    }

    set loading(activo) {
      clearTimeout(this._temporizadorCarga);
      if (!activo) {
        this._carga.hidden = true;
        return;
      }
      this._temporizadorCarga = window.setTimeout(() => {
        this._carga.hidden = false;
      }, 200);
    }
  }

  class ModuLinkrModbusAiAssistant extends ModuLinkrElement {
    connectedCallback() {
      if (this._iniciado) return;
      this._iniciado = true;
      this._dialogo = this.querySelector(".mbai-dialog");
      this._formulario = this.querySelector("#mbai-form");
      this._siguiente = this.querySelector('[data-mbai-action="next"]');
      this._volver = this.querySelector('[data-mbai-action="back"]');
      this._aplicarConfirmado = this.querySelector(
        '[data-mbai-action="apply-confirmed"]');
      this._requisito = this.querySelector("#mbai-requirement");
      this._accion = (evento) => this._manejarAccion(evento);
      this._cambio = (evento) => this._manejarCambio(evento);
      this._entrada = (evento) => this._manejarEntrada(evento);
      this._cancelar = (evento) => {
        evento.preventDefault();
        this._abortar();
        this._dialogo.close("cancel");
      };
      this._cerrado = () => this._alCerrar();
      this.addEventListener("click", this._accion);
      this.addEventListener("change", this._cambio);
      this.addEventListener("input", this._entrada);
      this._dialogo.addEventListener("cancel", this._cancelar);
      this._dialogo.addEventListener("close", this._cerrado);
    }

    disconnectedCallback() {
      this.removeEventListener("click", this._accion);
      this.removeEventListener("change", this._cambio);
      this.removeEventListener("input", this._entrada);
      this._dialogo?.removeEventListener("cancel", this._cancelar);
      this._dialogo?.removeEventListener("close", this._cerrado);
    }

    open(dispositivo) {
      this._focoAnterior = document.activeElement;
      this._dispositivo = dispositivo;
      this._formulario.reset();
      this._dialogo.returnValue = "";
      this._proposal = null;
      this._catalogProposal = null;
      this._discovery = null;
      this._selectedTargetId = null;
      this._sectionsExtracted = false;
      this._validation = null;
      this._sourceData = null;
      this._webQueries = new Set();
      this._configReady = false;
      this._busy = false;
      this.querySelectorAll("[data-mbai-source-panel]").forEach((panel) => {
        panel.hidden = true;
      });
      this.querySelector("#mbai-file-status").textContent =
        "El PDF se enviará al proveedor configurado cuando continúes. No se guardará en el gateway; el proveedor aplicará su política de datos.";
      const fileName = this.querySelector("#mbai-file-name");
      fileName.textContent = "Ningún archivo seleccionado";
      fileName.title = "";
      this.querySelector("#mbai-candidates-container").replaceChildren();
      this.querySelector("#mbai-candidates-container").hidden = true;
      this.querySelector("#mbai-sections-container").replaceChildren();
      this.querySelector("#mbai-targets-container").replaceChildren();
      this.querySelector("#mbai-source-summary").replaceChildren();
      this.querySelector("#mbai-evidence-content").replaceChildren();
      this.querySelector("#mbai-review-summary").replaceChildren();
      this.querySelector("#mbai-review-items").replaceChildren();
      this.querySelector("#mbai-review-correction-list").replaceChildren();
      this.querySelector("#mbai-review-preserved-list").replaceChildren();
      this.querySelector("#mbai-review-excluded-list").replaceChildren();
      this.querySelector("#mbai-review-corrections").hidden = true;
      this._aplicarConfirmado.hidden = true;

      const numero = dispositivo?.querySelector(".fdev-head strong")?.textContent
        || "Dispositivo Modbus";
      const nombre = dispositivo?.querySelector('[data-fd="name"]')?.value.trim();
      this.querySelector("#mbai-device-context").textContent = nombre
        ? `${numero}: ${nombre}` : numero;
      this._mostrarPaso(1);
      if (!this._dialogo.open) this._dialogo.showModal();
      requestAnimationFrame(() =>
        this.querySelector('input[name="mbai-source"]')?.focus());
      this._cargarEstado();
    }

    async _manejarAccion(evento) {
      const boton = evento.target.closest("[data-mbai-action]");
      if (!boton || !this.contains(boton)) return;
      const accion = boton.dataset.mbaiAction;
      if (this._busy && accion !== "cancel") return;
      try {
        if (accion === "cancel") {
          this._abortar();
          this._dialogo.close("cancel");
        } else if (accion === "back") {
          this._mostrarPaso(Math.max(1, this._paso - 1));
        } else if (accion === "next") {
          await this._avanzar();
        } else if (accion === "apply-confirmed") {
          this._aplicarPropuesta(true);
        }
      } catch (error) {
        if (error?.name !== "AbortError") this._mostrarError(error);
      }
    }

    _manejarCambio(evento) {
      if (evento.target.matches('input[name="mbai-source"]')) {
        const origen = evento.target.value;
        this._sourceData = null;
        this.querySelectorAll("[data-mbai-source-panel]").forEach((panel) => {
          panel.hidden = panel.dataset.mbaiSourcePanel !== origen;
        });
      }
      if (evento.target.id === "mbai-manual") {
        const archivo = evento.target.files?.[0];
        this._sourceData = null;
        const fileName = this.querySelector("#mbai-file-name");
        fileName.textContent = archivo?.name || "Ningún archivo seleccionado";
        fileName.title = archivo?.name || "";
        this.querySelector("#mbai-file-status").textContent = archivo
          ? (archivo.size <= 10 * 1024 * 1024
            ? `Seleccionado: ${archivo.name}. Se enviará al proveedor al continuar según su política de datos.`
            : "El PDF supera el límite de 10 MB.")
          : "El PDF se enviará al proveedor configurado cuando continúes. No se guardará en el gateway; el proveedor aplicará su política de datos.";
      }
      if (evento.target.matches('input[name="mbai-target"]')) {
        this._selectTarget(evento.target.value);
      }
      if (evento.target.matches('input[name="mbai-section"]')) {
        const checked = this.querySelectorAll(
          'input[name="mbai-section"]:checked');
        if (checked.length > 8) evento.target.checked = false;
      }
      this._actualizarControles();
    }

    _manejarEntrada(evento) {
      if (["mbai-source-manufacturer", "mbai-source-model"].includes(
        evento.target.id)) this._sourceData = null;
      this._actualizarControles();
    }

    async _avanzar() {
      if (!this._pasoValido()) return;
      if (this._paso === 4) {
        this._aplicarPropuesta(false);
        return;
      }
      if (this._paso === 1) {
        await this._solicitarDescubrimiento();
        this._prepararConfirmacion();
        this._mostrarPaso(2);
        return;
      }
      if (this._paso === 2) {
        this._renderSections();
        this._mostrarPaso(3);
        return;
      }
      if (this._paso === 3) {
        if (!this._sectionsExtracted) {
          await this._solicitarExtraccion();
          this._renderCandidates();
          this._sectionsExtracted = true;
          this.querySelector("#mbai-sections-container").hidden = true;
          this.querySelector("#mbai-candidates-container").hidden = false;
          this.querySelector("#mbai-parameters-intro").textContent =
            "Selecciona las lecturas y escrituras que quieres cargar.";
          this._mostrarPaso(3);
          return;
        }
        await this._prepararSeleccion();
        this._mostrarPaso(4);
      }
    }

    _prepararConfirmacion() {
      const origen = this.querySelector('input[name="mbai-source"]:checked')?.value;
      const nota = this.querySelector("#mbai-confirm-note");
      const targets = this._discovery?.targets || [];
      this._renderTargets();
      if (targets.length === 1) this._selectTarget(targets[0].id);
      else this._selectTarget(null);
      const scopeLabels = {
        single_model: "un único modelo",
        product_family: "una familia con varias variantes",
        multi_device_system: "varios dispositivos físicos",
        ambiguous: "un alcance que requiere confirmación",
      };
      const scope = scopeLabels[this._discovery?.document_scope]
        || "uno o varios dispositivos";
      nota.textContent = targets.length > 1
        ? `La fuente describe ${scope}. Selecciona el dispositivo exacto antes de buscar parámetros.`
        : (origen === "identity"
          ? "La investigación localizó este dispositivo. Confírmalo antes de buscar parámetros."
          : "El dispositivo se extrajo del manual. Confírmalo antes de buscar parámetros.");
      this._renderSourceSummary();
    }

    _renderTargets() {
      const container = this.querySelector("#mbai-targets-container");
      container.replaceChildren();
      const targets = this._discovery?.targets || [];
      if (!targets.length) return;
      const fieldset = document.createElement("fieldset");
      fieldset.className = "mbai-candidates mbai-targets";
      const legend = document.createElement("legend");
      legend.textContent = targets.length === 1
        ? "Dispositivo localizado" : "Dispositivos localizados";
      fieldset.appendChild(legend);
      targets.forEach((target) => {
        const label = document.createElement("label");
        label.className = "mbai-candidate";
        const input = document.createElement("input");
        input.type = "radio";
        input.name = "mbai-target";
        input.value = target.id;
        const text = document.createElement("span");
        const strong = document.createElement("strong");
        strong.textContent = target.label;
        const small = document.createElement("small");
        const revision = target.revision ? `, ${target.revision}` : "";
        small.textContent = target.description
          || `${target.manufacturer} ${target.model}${revision}`;
        text.append(strong, small);
        label.append(input, text);
        fieldset.appendChild(label);
      });
      container.appendChild(fieldset);
    }

    _selectTarget(targetId) {
      const target = (this._discovery?.targets || [])
        .find((item) => item.id === targetId) || null;
      this._selectedTargetId = target?.id || null;
      this.querySelector("#mbai-confirm-manufacturer").value =
        target?.manufacturer || "";
      this.querySelector("#mbai-confirm-model").value = target?.model || "";
      this.querySelector("#mbai-confirm-revision").value = target?.revision || "";
      this._proposal = null;
      this._catalogProposal = null;
      this._validation = null;
      this._sectionsExtracted = false;
      this.querySelector("#mbai-sections-container").replaceChildren();
      this.querySelector("#mbai-sections-container").hidden = false;
      this.querySelector("#mbai-candidates-container").replaceChildren();
      this.querySelector("#mbai-candidates-container").hidden = true;
      this.querySelector("#mbai-parameters-intro").textContent =
        "Selecciona los grupos del manual que quieres analizar.";
    }

    _selectedTarget() {
      return (this._discovery?.targets || [])
        .find((item) => item.id === this._selectedTargetId) || null;
    }

    _renderSections() {
      const container = this.querySelector("#mbai-sections-container");
      container.replaceChildren();
      container.hidden = false;
      this.querySelector("#mbai-candidates-container").hidden = true;
      this._sectionsExtracted = false;
      const sections = (this._discovery?.sections || []).filter((item) =>
        item.applicability === "catalog"
        && item.target_ids.includes(this._selectedTargetId));
      const fieldset = document.createElement("fieldset");
      fieldset.className = "mbai-candidates";
      const legend = document.createElement("legend");
      legend.textContent = "Grupos disponibles";
      fieldset.appendChild(legend);
      const categoryLabels = {
        measurement: "Mediciones",
        status: "Estados",
        operational_control: "Controles",
        metadata: "Identificación",
        communication: "Comunicación",
        other: "Otros",
      };
      let selectedByDefault = 0;
      sections.forEach((section) => {
        const label = document.createElement("label");
        label.className = "mbai-candidate";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.name = "mbai-section";
        input.value = section.id;
        const operational = [
          "measurement", "status", "operational_control",
        ].includes(section.category);
        input.checked = operational && selectedByDefault < 8;
        if (input.checked) selectedByDefault += 1;
        const text = document.createElement("span");
        const strong = document.createElement("strong");
        strong.textContent = section.title;
        const small = document.createElement("small");
        small.textContent = categoryLabels[section.category] || "Otros";
        text.append(strong, small);
        label.append(input, text);
        fieldset.appendChild(label);
      });
      if (sections.length) container.appendChild(fieldset);
      else {
        const empty = document.createElement("div");
        empty.className = "mensaje mensaje-advertencia mbai-message";
        empty.textContent =
          "No se localizaron grupos aplicables para el dispositivo seleccionado.";
        container.appendChild(empty);
      }
      this._actualizarControles();
    }

    _selectedSectionIds() {
      return [...this.querySelectorAll('input[name="mbai-section"]:checked')]
        .map((input) => input.value);
    }

    _sectionSelectionIssue() {
      if (this._sectionsExtracted) return null;
      const selectedIds = new Set(this._selectedSectionIds());
      if (selectedIds.size > 8) return "Selecciona como máximo ocho grupos.";
      return null;
    }

    _mostrarPaso(paso) {
      this._paso = paso;
      this.querySelectorAll("[data-mbai-step]").forEach((seccion) => {
        seccion.hidden = Number(seccion.dataset.mbaiStep) !== paso;
      });
      this.querySelectorAll("[data-mbai-progress]").forEach((elemento) => {
        const numero = Number(elemento.dataset.mbaiProgress);
        elemento.toggleAttribute("aria-current", numero === paso);
        elemento.classList.toggle("completed", numero < paso);
      });
      this._volver.hidden = paso === 1;
      if (paso === 4) {
        const hasCorrections = this._loadablePending().length > 0;
        this._siguiente.textContent = "Cargar en el formulario";
        this._aplicarConfirmado.hidden = !(hasCorrections
          && this._hasApplicableChanges(this._applicationProposal(true)));
      } else {
        this._siguiente.textContent = paso === 1
          ? "Analizar"
          : (paso === 3 && !this._sectionsExtracted
            ? "Buscar parámetros" : "Continuar");
        this._aplicarConfirmado.hidden = true;
      }
      this._actualizarControles();
      requestAnimationFrame(() =>
        this.querySelector(`[data-mbai-step="${paso}"] h3`)?.focus());
    }

    _pasoValido() {
      if (this._paso === 1) {
        if (!this._configReady) return false;
        const origen = this.querySelector('input[name="mbai-source"]:checked')?.value;
        if (origen === "manual") {
          const file = this.querySelector("#mbai-manual").files?.[0];
          return Boolean(file && file.size <= 10 * 1024 * 1024);
        }
        if (origen === "identity") {
          return Boolean(this.querySelector("#mbai-source-model").value.trim());
        }
        return false;
      }
      if (this._paso === 2) {
        return Boolean(this._selectedTarget()
          && this.querySelector("#mbai-confirm-manufacturer").value.trim()
          && this.querySelector("#mbai-confirm-model").value.trim());
      }
      if (this._paso === 3) {
        return this._sectionsExtracted
          ? Boolean(this._catalogHasEntries(this._catalogProposal)
            && this.querySelector('input[name="mbai-candidate"]:checked'))
          : this._selectedSectionIds().length > 0
            && !this._sectionSelectionIssue();
      }
      return this._validation?.ready === true
        && this._hasApplicableChanges(this._applicationProposal(false));
    }

    _actualizarControles() {
      this._requisito.classList.remove("mbai-error");
      const valido = this._pasoValido();
      this._siguiente.disabled = this._busy || !valido;
      if (this._busy) return;
      if (this._paso === 1) {
        const origen = this.querySelector('input[name="mbai-source"]:checked')?.value;
        if (!this._configReady) return;
        this._requisito.textContent = !origen
          ? "Selecciona cómo identificar el dispositivo."
          : (valido
            ? "Información preparada para continuar."
            : (origen === "identity"
              ? "Indica el modelo exacto del dispositivo."
              : "Selecciona el manual del dispositivo."));
      } else if (this._paso === 2) {
        const hasIdentity = Boolean(
          this.querySelector("#mbai-confirm-manufacturer").value.trim()
          && this.querySelector("#mbai-confirm-model").value.trim());
        this._requisito.textContent = !this._selectedTarget()
          ? "Selecciona el dispositivo exacto descrito por la fuente."
          : (!hasIdentity
            ? "No se pudo confirmar el fabricante y el modelo exactos."
            : "Dispositivo preparado para confirmar.");
      } else if (this._paso === 3) {
        const sectionIssue = this._sectionSelectionIssue();
        this._requisito.textContent = this._sectionsExtracted
          ? (valido
            ? "Selección preparada para continuar."
            : "Selecciona al menos una lectura o escritura.")
          : (sectionIssue || (valido
            ? `${this._selectedSectionIds().length} grupos preparados para analizar.`
            : "Selecciona al menos un grupo para analizar."));
      } else {
        const corrections = this._loadablePending().length;
        const confirmed = this._hasApplicableChanges(this._applicationProposal(true));
        this._requisito.textContent = corrections
          ? (confirmed
            ? "La propuesta completa está bloqueada. Solo pueden cargarse por separado los parámetros confirmados."
            : "La propuesta está bloqueada porque faltan datos obligatorios.")
          : (!valido
            ? "No hay datos completos que puedan cargarse en el formulario."
            : "Todo lo seleccionado quedó confirmado y está listo para cargar.");
      }
    }

    async _cargarEstado() {
      this._requisito.classList.remove("mbai-error");
      this._requisito.textContent = "Comprobando la configuración del proveedor...";
      try {
        const response = await fetch("/api/ia/estado", {
          credentials: "same-origin", headers: { Accept: "application/json" },
        });
        if (response.status === 401) {
          location.href = "/login";
          return;
        }
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "No se pudo comprobar el proveedor.");
        this._configReady = Boolean(data.configuration_complete && data.security_ready);
        if (!data.security_ready) {
          this._requisito.textContent = data.blocked_reason || "El asistente requiere autenticación y HTTPS.";
        } else if (!data.configuration_complete) {
          this._requisito.textContent = "Configura el modelo y la clave API en Configuración, Asistente de IA.";
        }
      } catch (error) {
        this._configReady = false;
        this._mostrarError(error);
      }
      this._actualizarControles();
    }

    _abortar() {
      this._controller?.abort();
      this._controller = null;
    }

    async _api(path, body) {
      this._abortar();
      this._controller = new AbortController();
      const response = await fetch(path, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
        signal: this._controller.signal,
      });
      if (response.status === 401) {
        location.href = "/login";
        throw new Error("La sesión ha caducado.");
      }
      let data;
      try {
        data = await response.json();
      } catch (_) {
        throw new Error("El servidor no devolvió una respuesta válida.");
      }
      if (!response.ok) throw new Error(data.error || `Error HTTP ${response.status}`);
      return data;
    }

    async _pdfBase64(file) {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 32768) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
      }
      return btoa(binary);
    }

    async _sourceRequest() {
      if (this._sourceData) return this._sourceData;
      const kind = this.querySelector('input[name="mbai-source"]:checked')?.value;
      if (kind === "manual") {
        const file = this.querySelector("#mbai-manual").files?.[0];
        if (!file) throw new Error("Selecciona el manual en PDF.");
        this._sourceData = {
          kind, manufacturer: null, model: null, filename: file.name,
          pdf_base64: await this._pdfBase64(file),
        };
      } else {
        this._sourceData = {
          kind: "identity",
          manufacturer: this.querySelector("#mbai-source-manufacturer").value.trim(),
          model: this.querySelector("#mbai-source-model").value.trim(),
          filename: null,
          pdf_base64: null,
        };
      }
      return this._sourceData;
    }

    _confirmedIdentity() {
      if (!this._selectedTarget()) return null;
      return {
        manufacturer: this.querySelector("#mbai-confirm-manufacturer").value.trim() || null,
        model: this.querySelector("#mbai-confirm-model").value.trim() || null,
        revision: this.querySelector("#mbai-confirm-revision").value.trim() || null,
      };
    }

    _catalogHasEntries(proposal) {
      return Boolean(proposal && Array.isArray(proposal.reads)
        && Array.isArray(proposal.writes)
        && proposal.reads.length + proposal.writes.length > 0);
    }

    _assertProposalResponse(data) {
      const proposal = data?.proposal;
      if (!proposal || !Array.isArray(proposal.reads)
          || !Array.isArray(proposal.writes)) {
        throw new Error(
          "No se pudo completar el análisis con seguridad. No se cargó ningún dato.");
      }
      if (!this._catalogHasEntries(proposal)) {
        throw new Error(
          "No se obtuvo un catálogo Modbus fiable con la información disponible. No se cargó ningún dato. Revisa el manual o vuelve a analizarlo.");
      }
    }

    _currentContext() {
      const detail = { device: this._dispositivo, context: {} };
      this.emit("modulinkr-modbus-ai-context", detail);
      return detail.context || {};
    }

    async _requestBody(operation, previous = null) {
      return {
        operation,
        source: await this._sourceRequest(),
        confirmed_identity: operation === "extract"
          ? this._confirmedIdentity() : null,
        current: this._currentContext(),
        discovery: operation === "extract" ? this._discovery : null,
        target_id: operation === "extract" ? this._selectedTargetId : null,
        selected_sections: operation === "extract"
          ? this._selectedSectionIds() : [],
        previous_proposal: previous,
        selected: previous ? {
          reads: previous.reads.map((entry) => entry.id),
          writes: previous.writes.map((entry) => entry.id),
        } : { reads: [], writes: [] },
        answers: [],
        web_queries: [...this._webQueries],
      };
    }

    async _solicitarDescubrimiento() {
      this._setBusy(true,
        "Revisando el alcance del documento y localizando dispositivos y secciones Modbus...");
      try {
        const data = await this._api(
          "/api/ia/modbus/proponer",
          await this._requestBody("discover"));
        if (!data?.discovery || !Array.isArray(data.discovery.targets)
            || !Array.isArray(data.discovery.sections)
            || !data.discovery.targets.length) {
          throw new Error(
            "No se pudo identificar con seguridad qué dispositivo describe la fuente.");
        }
        this._discovery = data.discovery;
      } finally {
        this._setBusy(false);
      }
    }

    async _solicitarExtraccion() {
      this._setBusy(true,
        "Extrayendo los parámetros del dispositivo y los grupos seleccionados...");
      try {
        const data = await this._api(
          "/api/ia/modbus/proponer",
          await this._requestBody("extract"));
        this._assertProposalResponse(data);
        this._proposal = data.proposal;
        this._validation = data;
        this._catalogProposal = JSON.parse(JSON.stringify(data.proposal));
        this._webQueries.clear();
      } finally {
        this._setBusy(false);
      }
    }

    async _solicitarPropuesta(previous) {
      this._setBusy(true, this._webQueries.size
        ? "Investigando automáticamente los datos seleccionados y contrastando las fuentes..."
        : "Completando los parámetros seleccionados y contrastando las fuentes...");
      try {
        const data = await this._api(
          "/api/ia/modbus/proponer",
          await this._requestBody("refine", previous));
        this._assertProposalResponse(data);
        this._proposal = data.proposal;
        this._validation = data;
        this._webQueries.clear();
      } finally {
        this._setBusy(false);
      }
    }

    _filterProposal() {
      const proposal = JSON.parse(JSON.stringify(
        this._catalogProposal || this._proposal));
      const reads = new Set([...this.querySelectorAll(
        'input[name="mbai-candidate"][data-kind="reads"]:checked')]
        .map((input) => input.value));
      const writes = new Set([...this.querySelectorAll(
        'input[name="mbai-candidate"][data-kind="writes"]:checked')]
        .map((input) => input.value));
      proposal.reads = proposal.reads.filter((entry) => reads.has(entry.id));
      proposal.writes = proposal.writes.filter((entry) => writes.has(entry.id));
      proposal.pending = proposal.pending.filter((item) => {
        if (item.field.startsWith("identity.")) return false;
        const match = item.field.match(/^(reads|writes)\.([a-z][a-z0-9_]*)\./);
        if (!match) return true;
        return match[1] === "reads" ? reads.has(match[2]) : writes.has(match[2]);
      });
      return proposal;
    }

    async _prepararSeleccion() {
      const selection = this._filterProposal();
      this._proposal = selection;
      const pending = selection.pending || [];
      if (pending.length) {
        this._webQueries = new Set(pending
          .filter((item) => item.can_research_web && item.web_query)
          .map((item) => item.web_query));
        await this._solicitarPropuesta(selection);
      } else {
        this._webQueries.clear();
        this._setBusy(true, "Validando localmente los parámetros seleccionados...");
        try {
          const data = await this._api(
            "/api/ia/modbus/validar", { proposal: selection });
          this._assertProposalResponse(data);
          this._proposal = data.proposal;
          this._validation = data;
        } finally {
          this._setBusy(false);
        }
      }
      this._renderReview();
    }

    _renderSourceSummary() {
      const container = this.querySelector("#mbai-source-summary");
      container.replaceChildren();
      const sources = this._discovery?.sources || [];
      if (!sources.length) return;
      const title = document.createElement("strong");
      title.textContent = sources.length === 1 ? "Fuente utilizada" : "Fuentes utilizadas";
      const list = document.createElement("ul");
      sources.forEach((source) => {
        const item = document.createElement("li");
        item.textContent = source.title;
        list.appendChild(item);
      });
      container.append(title, list);
    }

    _candidateFieldset(kind, title, entries) {
      const fieldset = document.createElement("fieldset");
      fieldset.className = "mbai-candidates";
      const legend = document.createElement("legend");
      legend.textContent = title;
      fieldset.appendChild(legend);
      entries.forEach((entry) => {
        const label = document.createElement("label");
        label.className = "mbai-candidate";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.name = "mbai-candidate";
        input.dataset.kind = kind;
        input.value = entry.id;
        input.checked = kind === "reads";
        const text = document.createElement("span");
        const strong = document.createElement("strong");
        strong.textContent = entry.name || entry.id;
        const small = document.createElement("small");
        const address = entry.address == null ? "dirección pendiente" : `dirección ${entry.address}`;
        const functions = {
          read_coils: "Leer bobinas",
          read_discrete_inputs: "Leer entradas discretas",
          read_holding_registers: "Leer registros de retención",
          read_input_registers: "Leer registros de entrada",
          write_single_coil: "Escribir una bobina",
          write_multiple_coils: "Escribir varias bobinas",
          write_single_register: "Escribir un registro",
          write_multiple_registers: "Escribir varios registros",
        };
        small.textContent = `${functions[entry.function] || "Función pendiente"}, ${address}`;
        text.append(strong, small);
        const badge = document.createElement("em");
        badge.textContent = kind === "reads" ? "Lectura" : "Escritura";
        label.append(input, text, badge);
        fieldset.appendChild(label);
      });
      return fieldset;
    }

    _renderCandidates() {
      const container = this.querySelector("#mbai-candidates-container");
      container.replaceChildren();
      const catalog = this._catalogProposal || this._proposal;
      const reads = catalog?.reads || [];
      const writes = catalog?.writes || [];
      if (reads.length) container.appendChild(
        this._candidateFieldset("reads", "Lecturas localizadas", reads));
      if (writes.length) container.appendChild(
        this._candidateFieldset("writes", "Escrituras localizadas", writes));
      if (!reads.length && !writes.length) {
        const empty = document.createElement("div");
        empty.className = "mensaje mensaje-advertencia mbai-message";
        empty.textContent = "El análisis no produjo parámetros que puedan cargarse con seguridad. No se modificó el formulario.";
        container.appendChild(empty);
      }
    }

    _renderEvidence() {
      const container = this.querySelector("#mbai-evidence-content");
      container.replaceChildren();
      const sources = this._uniqueSources({
        sources: [
          ...(this._catalogProposal?.sources || []),
          ...(this._validation?.proposal?.sources || []),
        ],
      });
      const list = document.createElement("ul");
      sources.forEach((source) => {
        const item = document.createElement("li");
        if (source.url) {
          const link = document.createElement("a");
          link.href = source.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = source.title;
          item.appendChild(link);
        } else {
          item.textContent = source.title;
        }
        list.appendChild(item);
      });
      if (sources.length) container.appendChild(list);
      else {
        const empty = document.createElement("p");
        empty.className = "mbai-review-empty";
        empty.textContent = "No se declaró una fuente utilizable para los elementos seleccionados.";
        container.appendChild(empty);
      }
    }

    _uniqueSources(proposal = this._proposal) {
      const seen = new Set();
      return (proposal?.sources || []).filter((source) => {
        const key = [
          source.kind || "",
          String(source.title || "").trim().toLocaleLowerCase("es"),
          String(source.url || "").trim().toLocaleLowerCase("es"),
        ].join("|");
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    }

    _uniqueUnsupported(proposal = this._proposal) {
      const seen = new Set();
      return (proposal?.unsupported || []).filter((entry) => {
        const key = [
          entry.category || "other",
          String(entry.summary || "").trim().toLocaleLowerCase("es"),
        ].join("|");
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    }

    _unsupportedText(category) {
      return ({
        bus_conflict: "La configuración documentada del dispositivo no coincide con la línea. Se conservaron los parámetros de la línea.",
        catalog_limit: "El documento contiene más parámetros de los que caben en una propuesta. Solo se muestran los que quedaron claramente documentados.",
        communication: "Se detectó un ajuste de dirección o comunicación. Se informa, pero no se añade como escritura operativa.",
        data_shape: "Se detectó un dato cuyo tamaño o estructura no puede representarse sin cambiar su significado.",
        mask: "Se detectó una escritura con máscara y no se aplicará automáticamente.",
        password: "Se detectó una operación protegida por contraseña y no se aplicará automáticamente.",
        unlock: "Se detectó una secuencia de desbloqueo y no se aplicará automáticamente.",
        sequence: "Se detectó una operación de varios pasos y no se aplicará automáticamente.",
        timing: "Se detectó un requisito de temporización o reinicio y no se aplicará automáticamente.",
        verification: "Se detectó una verificación posterior y no se aplicará automáticamente.",
        other: "Se detectó un parámetro sin evidencia suficiente para añadirlo de forma segura.",
      })[category] || "Se detectó un parámetro que no puede añadirse de forma segura.";
    }

    _pendingCopy(item) {
      const exact = {
        "identity.manufacturer": "¿Cuál es el fabricante exacto?",
        "identity.model": "¿Cuál es el modelo exacto?",
        "identity.revision": "¿Qué variante o revisión tiene el dispositivo?",
        "bus.baudrate": "¿Qué velocidad Modbus utiliza el dispositivo?",
        "bus.parity": "¿Qué paridad Modbus utiliza el dispositivo?",
        "bus.stopbits": "¿Cuántos bits de parada utiliza el dispositivo?",
        "device.name": "¿Qué nombre quieres usar para el dispositivo?",
        "device.description": "¿Qué descripción quieres usar para el dispositivo?",
        "device.default_slave_id": "¿Cuál es la dirección Modbus actual del dispositivo?",
        "device.desired_slave_id": "¿Qué dirección Modbus quieres asignarle?",
        "device.change_function": "¿Qué función Modbus permite cambiar la dirección?",
        "device.change_address": "¿En qué registro se cambia la dirección Modbus?",
        "device.read_mode": "¿Qué modo de lectura debe utilizarse?",
        "device.inter_read_ms": "¿Qué pausa debe dejarse entre lecturas?",
      };
      if (exact[item.field]) return {
        question: exact[item.field],
        reason: "Este dato no queda confirmado en las fuentes disponibles.",
      };
      const match = item.field.match(/^(reads|writes)\.([a-z][a-z0-9_]*)\.(.+)$/);
      if (!match) return {
        question: "¿Qué dato debe utilizarse?",
        reason: "La IA necesita confirmarlo antes de completar el formulario.",
      };
      const collection = match[1];
      const entry = (this._validation?.proposal?.[collection] || [])
        .find((candidate) => candidate.id === match[2]);
      const name = entry?.name || match[2];
      const questions = {
        id: `¿Qué identificador debe usarse para “${name}”?`,
        name: `¿Qué nombre debe mostrarse para “${name}”?`,
        function: `¿Qué función Modbus utiliza “${name}”?`,
        address: `¿Cuál es la dirección Modbus de “${name}”?`,
        count: `¿Cuántos registros ocupa “${name}”?`,
        type: `¿Qué tipo de dato utiliza “${name}”?`,
        byte_order: `¿Qué orden de bytes utiliza “${name}”?`,
        scale: `¿Qué escala utiliza “${name}”?`,
        offset: `¿Qué desplazamiento utiliza “${name}”?`,
        unit: `¿Qué unidad utiliza “${name}”?`,
      };
      return {
        question: questions[match[3]] || `¿Qué valor debe utilizar “${name}”?`,
        reason: "Este dato no queda confirmado en las fuentes disponibles.",
      };
    }

    _entryPending(kind, entry, proposal = this._validation?.proposal) {
      if (!entry?.id) return [];
      const prefix = `${kind}.${entry.id}.`;
      return (proposal?.pending || []).filter((item) =>
        String(item.field || "").startsWith(prefix));
    }

    _entryCanLoad(kind, entry, proposal = this._validation?.proposal) {
      const functions = kind === "reads"
        ? ["read_coils", "read_discrete_inputs", "read_holding_registers", "read_input_registers"]
        : ["write_single_coil", "write_multiple_coils", "write_single_register", "write_multiple_registers"];
      return Boolean(entry && typeof entry.id === "string" && entry.id
        && functions.includes(entry.function)
        && Array.isArray(entry.evidence) && entry.evidence.length
        && !this._entryPending(kind, entry, proposal)
          .some((item) => item.field.endsWith(".function")));
    }

    _applicationProposal(confirmedOnly = false) {
      const original = this._validation?.proposal;
      if (!original) return null;
      const proposal = JSON.parse(JSON.stringify(original));
      const kept = { reads: new Set(), writes: new Set() };
      ["reads", "writes"].forEach((kind) => {
        proposal[kind] = proposal[kind].filter((entry) => {
          if (!this._entryCanLoad(kind, entry, original)) return false;
          if (confirmedOnly && this._entryPending(kind, entry, original).length) return false;
          kept[kind].add(entry.id);
          return true;
        });
      });

      proposal.bus.baudrate = null;
      proposal.bus.parity = null;
      proposal.bus.stopbits = null;
      proposal.device.default_slave_id = null;
      proposal.device.desired_slave_id = null;

      const deviceFields = new Set([
        "name", "description", "change_function", "change_address",
        "read_mode", "inter_read_ms",
      ]);
      if (confirmedOnly) {
        (proposal.pending || []).forEach((item) => {
          const match = String(item.field || "").match(/^device\.(.+)$/);
          if (match && deviceFields.has(match[1])) proposal.device[match[1]] = null;
        });
        proposal.pending = [];
      } else {
        proposal.pending = (proposal.pending || []).filter((item) => {
          const field = String(item.field || "");
          const device = field.match(/^device\.(.+)$/);
          if (device) return deviceFields.has(device[1]);
          const entry = field.match(/^(reads|writes)\.([a-z][a-z0-9_]*)\.(.+)$/);
          return Boolean(entry && kept[entry[1]].has(entry[2]) && entry[3] !== "function");
        });
      }
      return proposal;
    }

    _hasApplicableChanges(proposal) {
      return Boolean((proposal?.reads || []).length
        || (proposal?.writes || []).length);
    }

    _loadablePending() {
      return this._applicationProposal(false)?.pending || [];
    }

    _aplicarPropuesta(confirmedOnly) {
      if (!confirmedOnly && this._validation?.ready !== true) {
        throw new Error(
          "La propuesta contiene datos obligatorios sin confirmar y no puede cargarse."
        );
      }
      const proposal = this._applicationProposal(confirmedOnly);
      if (!this._hasApplicableChanges(proposal)) {
        throw new Error("No hay datos confirmados que puedan cargarse en el formulario.");
      }
      const detail = {
        device: this._dispositivo,
        proposal,
        mode: confirmedOnly ? "confirmed" : "review",
        applied: false,
        error: "",
      };
      this.emit("modulinkr-modbus-ai-apply", detail);
      if (!detail.applied) throw new Error(
        detail.error || "No se pudo cargar la propuesta en el formulario.");
      this._dialogo.close("complete");
    }

    _reviewUnsupported() {
      return this._uniqueUnsupported({
        unsupported: [
          ...(this._catalogProposal?.unsupported || []),
          ...(this._validation?.proposal?.unsupported || []),
        ],
      });
    }

    _unloadableEntries() {
      const proposal = this._validation?.proposal;
      if (!proposal) return [];
      const result = [];
      ["reads", "writes"].forEach((kind) => {
        (proposal[kind] || []).forEach((entry) => {
          if (this._entryCanLoad(kind, entry, proposal)) return;
          const pendingFunction = this._entryPending(kind, entry, proposal)
            .some((item) => item.field.endsWith(".function"));
          let reason = "El dato no puede colocarse en un campo concreto sin alterar su significado.";
          if (!Array.isArray(entry.evidence) || !entry.evidence.length) {
            reason = "No se declaró una fuente que confirme este parámetro.";
          } else if (pendingFunction || !entry.function) {
            reason = "No se confirmó la función Modbus y no se puede determinar en qué grupo del formulario debe aparecer.";
          } else if (!entry.id) {
            reason = "No se obtuvo un identificador utilizable para el formulario.";
          }
          result.push({
            category: "other",
            summary: `${kind === "reads" ? "Lectura" : "Escritura"}: ${entry.name || entry.id || "parámetro sin identificar"}`,
            reason,
          });
        });
      });
      return result;
    }

    _functionText(value) {
      return ({
        read_coils: "Leer bobinas",
        read_discrete_inputs: "Leer entradas discretas",
        read_holding_registers: "Leer registros de retención",
        read_input_registers: "Leer registros de entrada",
        write_single_coil: "Escribir una bobina",
        write_multiple_coils: "Escribir varias bobinas",
        write_single_register: "Escribir un registro",
        write_multiple_registers: "Escribir varios registros",
      })[value] || "Función sin confirmar";
    }

    _reviewEntryCard(kind, entry) {
      const pending = this._entryPending(kind, entry);
      const card = document.createElement("article");
      card.className = "mbai-review-item";
      const head = document.createElement("div");
      head.className = "mbai-review-item-head";
      const title = document.createElement("strong");
      title.textContent = entry.name || entry.id;
      const badge = document.createElement("span");
      badge.className = `mbai-review-badge${pending.length ? " correction" : ""}`;
      badge.textContent = pending.length ? "Por corregir" : "Confirmado";
      head.append(title, badge);
      const meta = document.createElement("p");
      meta.className = "mbai-review-meta";
      const address = entry.address == null ? "dirección pendiente" : `dirección ${entry.address}`;
      const count = entry.count == null ? "cantidad pendiente" : `cantidad ${entry.count}`;
      const format = entry.type
        ? `${entry.type}${entry.byte_order ? `, orden ${entry.byte_order}` : ""}`
        : "tipo pendiente";
      meta.textContent = `${kind === "reads" ? "Lectura" : "Escritura"}. ${this._functionText(entry.function)}, ${address}, ${count}, ${format}.`;
      card.append(head, meta);
      if (pending.length) {
        const detail = document.createElement("p");
        detail.className = "mbai-review-reason";
        detail.textContent = `Falta confirmar: ${pending.map((item) =>
          this._pendingCopy(item).question.replace(/^¿|\?$/g, "").toLocaleLowerCase("es"))
          .join("; ")}.`;
        card.appendChild(detail);
      }
      return card;
    }

    _renderReviewItems() {
      const container = this.querySelector("#mbai-review-items");
      container.replaceChildren();
      const proposal = this._applicationProposal(true);
      ["reads", "writes"].forEach((kind) => {
        (proposal?.[kind] || []).forEach((entry) =>
          container.appendChild(this._reviewEntryCard(kind, entry)));
      });
      if (!container.childElementCount) {
        const empty = document.createElement("p");
        empty.className = "mbai-review-empty";
        empty.textContent = "No hay lecturas ni escrituras que puedan cargarse con seguridad.";
        container.appendChild(empty);
      }
    }

    _renderCorrections() {
      const section = this.querySelector("#mbai-review-corrections");
      const container = this.querySelector("#mbai-review-correction-list");
      container.replaceChildren();
      const pending = this._loadablePending();
      section.hidden = !pending.length;
      pending.forEach((item) => {
        const copy = this._pendingCopy(item);
        const card = document.createElement("article");
        card.className = "mbai-review-item";
        const head = document.createElement("div");
        head.className = "mbai-review-item-head";
        const title = document.createElement("strong");
        title.textContent = copy.question;
        const badge = document.createElement("span");
        badge.className = "mbai-review-badge correction";
        badge.textContent = "Sin confirmar";
        head.append(title, badge);
        const reason = document.createElement("p");
        reason.className = "mbai-review-reason";
        reason.textContent = item.reason || copy.reason;
        card.append(head, reason);
        container.appendChild(card);
      });
    }

    _renderPreserved() {
      const container = this.querySelector("#mbai-review-preserved-list");
      container.replaceChildren();
      const context = this._currentContext();
      const bus = context.bus || {};
      const parity = ({ N: "sin paridad", E: "paridad par", O: "paridad impar" })[
        String(bus.parity || "").toUpperCase()] || `paridad ${bus.parity || "sin indicar"}`;
      const stopbits = Number(bus.stopbits);
      const line = document.createElement("ul");
      const busItem = document.createElement("li");
      busItem.textContent = bus.baudrate
        ? `Línea Modbus: ${bus.baudrate} baud, ${parity}, ${stopbits || "sin indicar"} ${stopbits === 1 ? "bit" : "bits"} de parada.`
        : "Los parámetros comunes de la línea Modbus no se modifican desde este asistente.";
      line.appendChild(busItem);
      const current = context.device || {};
      const address = document.createElement("li");
      const actual = current.default_slave_id;
      const desired = current.desired_slave_id;
      address.textContent = actual && desired
        ? `Direcciones del dispositivo: actual ${actual} y deseada ${desired}.`
        : "Las direcciones actual y deseada del dispositivo se conservan.";
      line.appendChild(address);
      container.appendChild(line);
    }

    _unsupportedLabel(category) {
      return ({
        bus_conflict: "Conflicto con la línea",
        catalog_limit: "Catálogo parcial",
        communication: "Ajuste de comunicación",
        data_shape: "Formato no compatible",
        mask: "Escritura con máscara",
        password: "Operación con contraseña",
        unlock: "Secuencia de desbloqueo",
        sequence: "Operación de varios pasos",
        timing: "Temporización o reinicio",
        verification: "Verificación posterior",
        other: "Elemento no aplicable",
      })[category] || "Elemento no aplicable";
    }

    _renderExcluded() {
      const container = this.querySelector("#mbai-review-excluded-list");
      container.replaceChildren();
      const unsupported = [...this._reviewUnsupported(), ...this._unloadableEntries()];
      if (!unsupported.length) {
        const empty = document.createElement("p");
        empty.className = "mbai-review-empty";
        empty.textContent = "No se detectaron elementos excluidos.";
        container.appendChild(empty);
        return;
      }
      unsupported.forEach((entry) => {
        const card = document.createElement("article");
        card.className = "mbai-review-item";
        const head = document.createElement("div");
        head.className = "mbai-review-item-head";
        const title = document.createElement("strong");
        title.textContent = entry.summary || this._unsupportedLabel(entry.category);
        const badge = document.createElement("span");
        badge.className = "mbai-review-badge excluded";
        badge.textContent = this._unsupportedLabel(entry.category);
        head.append(title, badge);
        const reason = document.createElement("p");
        reason.className = "mbai-review-reason";
        reason.textContent = entry.reason || this._unsupportedText(entry.category);
        card.append(head, reason);
        container.appendChild(card);
      });
    }

    _renderReviewSummary() {
      const container = this.querySelector("#mbai-review-summary");
      container.replaceChildren();
      const proposal = this._applicationProposal(true);
      const reads = proposal?.reads?.length || 0;
      const writes = proposal?.writes?.length || 0;
      const corrections = this._loadablePending().length;
      const excluded = this._reviewUnsupported().length + this._unloadableEntries().length;
      const applicable = this._hasApplicableChanges(proposal);
      container.className = `mbai-review-summary${!applicable ? " blocked" : (corrections ? " warning" : "")}`;
      const title = document.createElement("strong");
      const detail = document.createElement("p");
      if (!applicable) {
        title.textContent = "No hay datos que puedan cargarse";
        detail.textContent = "La revisión explica debajo qué elementos quedaron fuera y por qué.";
      } else if (corrections) {
        title.textContent = "La propuesta completa está bloqueada";
        detail.textContent = `${corrections} ${corrections === 1 ? "dato obligatorio no está confirmado" : "datos obligatorios no están confirmados"}. No se cargará ningún campo vacío.`;
        if (reads || writes) {
          detail.textContent += ` ${reads} ${reads === 1 ? "lectura confirmada" : "lecturas confirmadas"} y ${writes} ${writes === 1 ? "escritura confirmada" : "escrituras confirmadas"} pueden cargarse por separado.`;
        }
      } else {
        title.textContent = "Propuesta lista para cargar";
        detail.textContent = `${reads} ${reads === 1 ? "lectura" : "lecturas"} y ${writes} ${writes === 1 ? "escritura" : "escrituras"} se cargarán en el formulario.`;
      }
      if (excluded) detail.textContent += ` ${excluded} ${excluded === 1 ? "elemento quedará" : "elementos quedarán"} fuera con su motivo.`;
      container.append(title, detail);
    }

    _renderReview() {
      this._renderReviewSummary();
      this._renderReviewItems();
      this._renderCorrections();
      this._renderPreserved();
      this._renderExcluded();
      this._renderEvidence();
      const hasCorrections = this._loadablePending().length > 0;
      this._aplicarConfirmado.hidden = !(hasCorrections
        && this._hasApplicableChanges(this._applicationProposal(true)));
    }

    _setBusy(active, message = "") {
      this._busy = active;
      this._formulario.inert = active;
      this._volver.disabled = active;
      if (active) {
        this._requisito.classList.remove("mbai-error");
        this._requisito.textContent = message;
        this._siguiente.disabled = true;
      } else {
        this._actualizarControles();
      }
    }

    _mostrarError(error) {
      this._busy = false;
      this._formulario.inert = false;
      this._volver.disabled = false;
      this._requisito.classList.add("mbai-error");
      this._requisito.textContent = error?.message || "No se pudo completar la operación.";
      this._siguiente.disabled = !this._pasoValido();
    }

    _alCerrar() {
      this._abortar();
      requestAnimationFrame(() => this._focoAnterior?.focus?.({ preventScroll: true }));
      this._focoAnterior = null;
      this._dispositivo = null;
      this._sourceData = null;
      this._proposal = null;
      this._catalogProposal = null;
      this._catalogIdentity = "";
      this._validation = null;
      this._webQueries?.clear();
    }
  }

  class ModuLinkrOverlay extends ModuLinkrElement {
    connectedCallback() {
      this._close = this.querySelector("[data-close]");
      this._backdrop = document.getElementById(this.getAttribute("backdrop"));
      this._requestClose = () => this.emit("modulinkr-close-request");
      this._close?.addEventListener("click", this._requestClose);
      this._backdrop?.addEventListener("click", this._requestClose);
    }

    disconnectedCallback() {
      this._close?.removeEventListener("click", this._requestClose);
      this._backdrop?.removeEventListener("click", this._requestClose);
    }

    show() {
      this.hidden = false;
      if (this._backdrop) this._backdrop.hidden = false;
      this.querySelector("[data-close]")?.focus();
    }

    hide() {
      this.hidden = true;
      if (this._backdrop) this._backdrop.hidden = true;
    }
  }

  class ModuLinkrNodeDetail extends ModuLinkrOverlay {
    connectedCallback() {
      super.connectedCallback();
      this.setAttribute("role", "dialog");
      this.setAttribute("aria-modal", "true");
      this.setAttribute("aria-labelledby", "detalle-titulo");
      this._hideTimer = null;
      this._previousFocus = null;
    }

    show() {
      clearTimeout(this._hideTimer);
      this._previousFocus = document.activeElement;
      this.hidden = false;
      if (this._backdrop) this._backdrop.hidden = false;
      requestAnimationFrame(() => {
        this.classList.add("abierto");
        this.focus({ preventScroll: true });
      });
    }

    hide() {
      this.classList.remove("abierto");
      if (this._backdrop) this._backdrop.hidden = true;
      this._hideTimer = window.setTimeout(() => {
        this.hidden = true;
        this._previousFocus?.focus?.({ preventScroll: true });
        this._previousFocus = null;
      }, 220);
    }
  }

  class ModuLinkrHistoryDialog extends ModuLinkrOverlay {
    connectedCallback() {
      super.connectedCallback();
      this.setAttribute("role", "dialog");
      this.setAttribute("aria-modal", "true");
      this.setAttribute("aria-labelledby", "modal-titulo");
    }
  }

  class ModuLinkrConfirmDialog extends ModuLinkrElement {
    connectedCallback() {
      this.setAttribute("role", "dialog");
      this.setAttribute("aria-modal", "true");
      this.setAttribute("aria-labelledby", "cfg-dialogo-titulo");
    }

    show() {
      this.hidden = false;
      const fondo = document.getElementById("cfg-dialogo-fondo");
      if (fondo) fondo.hidden = false;
    }

    hide() {
      this.hidden = true;
      const fondo = document.getElementById("cfg-dialogo-fondo");
      if (fondo) fondo.hidden = true;
    }
  }

  class ModuLinkrToastRegion extends ModuLinkrElement {
    connectedCallback() {
      this.setAttribute("role", "status");
      this.setAttribute("aria-live", "polite");
      this.setAttribute("aria-atomic", "true");
    }

    show(mensaje, tipo = "exito", duracion = 4000) {
      const aviso = document.createElement("div");
      aviso.className = "toast " + tipo;
      aviso.textContent = mensaje;
      this.appendChild(aviso);
      window.setTimeout(() => aviso.remove(), duracion);
    }
  }

  class ModuLinkrLoginPage extends ModuLinkrElement {
    connectedCallback() {
      if (new URLSearchParams(location.search).has("e")) {
        document.body.classList.add("con-error");
      }
    }
  }

  const componentes = {
    "modulinkr-icon": ModuLinkrIcon,
    "modulinkr-app": ModuLinkrApp,
    "modulinkr-sidebar": ModuLinkrSidebar,
    "modulinkr-app-header": ModuLinkrAppHeader,
    "modulinkr-view-router": ModuLinkrViewRouter,
    "modulinkr-view": ModuLinkrView,
    "modulinkr-node-card": ModuLinkrNodeCard,
    "modulinkr-measurement": ModuLinkrMeasurement,
    "modulinkr-measure-picker": ModuLinkrMeasurePicker,
    "modulinkr-chart-legend": ModuLinkrChartLegend,
    "modulinkr-period-selector": ModuLinkrPeriodSelector,
    "modulinkr-modbus-ai-assistant": ModuLinkrModbusAiAssistant,
    "modulinkr-node-detail": ModuLinkrNodeDetail,
    "modulinkr-history-dialog": ModuLinkrHistoryDialog,
    "modulinkr-confirm-dialog": ModuLinkrConfirmDialog,
    "modulinkr-toast-region": ModuLinkrToastRegion,
    "modulinkr-login-page": ModuLinkrLoginPage,
  };

  for (const [nombre, componente] of Object.entries(componentes)) {
    if (!customElements.get(nombre)) customElements.define(nombre, componente);
  }
}());
