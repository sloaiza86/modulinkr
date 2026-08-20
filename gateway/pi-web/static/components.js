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
      const nombre = referencia.startsWith("mdi:") ? referencia.slice(4) : "";
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
        let camino = await ModuLinkrIcon.path(nombre);
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
      if (!badge || !total) {
        if (badge) badge.hidden = true;
        return;
      }
      badge.textContent = `${online}/${total} en línea`;
      badge.className = "badge " + (online === total
        ? "" : (online === 0 ? "bad" : "warn"));
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
            icono: "access-point",
          });
        }
      }
      return [...grupos.values()];
    }

    _crearIcono(nombre) {
      const icono = document.createElement("modulinkr-icon");
      icono.setAttribute("name", `mdi:${nombre}`);
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
    "modulinkr-period-selector": ModuLinkrPeriodSelector,
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
