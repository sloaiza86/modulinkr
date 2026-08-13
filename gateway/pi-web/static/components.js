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
      this._toggle = () => this.toggle();
      this._menu?.addEventListener("click", this._toggle);
      this.collapsed = localStorage.getItem("modulinkr_sb") === "1";
    }

    disconnectedCallback() {
      this._menu?.removeEventListener("click", this._toggle);
    }

    set collapsed(valor) {
      const activo = Boolean(valor);
      document.body.classList.toggle("sb-contraida", activo);
      if (this._menu) {
        this._menu.title = activo ? "Expandir menú" : "Contraer menú";
        this._menu.setAttribute("aria-expanded", String(!activo));
      }
    }

    get collapsed() {
      return document.body.classList.contains("sb-contraida");
    }

    toggle() {
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
      this.setAttribute("role", "button");
      this.setAttribute("tabindex", "0");
      this.setAttribute("aria-label", this._label());
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
      this.emit("modulinkr-node-open", {
        origin: Number(this.dataset.origin),
      });
    };

    _onKeydown = (evento) => {
      if (evento.target !== this) return;
      if (evento.key === "Enter" || evento.key === " ") {
        evento.preventDefault();
        this.click();
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
