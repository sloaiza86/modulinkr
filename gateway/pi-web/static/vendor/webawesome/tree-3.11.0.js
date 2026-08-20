/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Xt=class extends Event{constructor(e){super("wa-selection-change",{bubbles:!0,cancelable:!1,composed:!0}),this.detail=e}};/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Yt=class extends Event{constructor(){super("wa-lazy-change",{bubbles:!0,cancelable:!1,composed:!0})}};/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Kt=class extends Event{constructor(){super("wa-lazy-load",{bubbles:!0,cancelable:!1,composed:!0})}};/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Gt=class extends Event{constructor(){super("wa-expand",{bubbles:!0,cancelable:!1,composed:!0})}};/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Zt=class extends Event{constructor(){super("wa-collapse",{bubbles:!0,cancelable:!1,composed:!0})}};/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Jt=class extends Event{constructor(){super("wa-after-collapse",{bubbles:!0,cancelable:!1,composed:!0})}};/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Qt=class extends Event{constructor(){super("wa-after-expand",{bubbles:!0,cancelable:!1,composed:!0})}};/**
 * @license
 * Copyright 2019 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var dt=globalThis,ht=dt.ShadowRoot&&(dt.ShadyCSS===void 0||dt.ShadyCSS.nativeShadow)&&"adoptedStyleSheets"in Document.prototype&&"replace"in CSSStyleSheet.prototype,$t=Symbol(),te=new WeakMap,G=class{constructor(t,i,o){if(this._$cssResult$=!0,o!==$t)throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");this.cssText=t,this.t=i}get styleSheet(){let t=this.o,i=this.t;if(ht&&t===void 0){let o=i!==void 0&&i.length===1;o&&(t=te.get(i)),t===void 0&&((this.o=t=new CSSStyleSheet).replaceSync(this.cssText),o&&te.set(i,t))}return t}toString(){return this.cssText}},ee=e=>new G(typeof e=="string"?e:e+"",void 0,$t),w=(e,...t)=>{let i=e.length===1?e[0]:t.reduce((o,n,s)=>o+(a=>{if(a._$cssResult$===!0)return a.cssText;if(typeof a=="number")return a;throw Error("Value passed to 'css' function must be a 'css' function result: "+a+". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.")})(n)+e[s+1],e[0]);return new G(i,e,$t)},ie=(e,t)=>{if(ht)e.adoptedStyleSheets=t.map(i=>i instanceof CSSStyleSheet?i:i.styleSheet);else for(let i of t){let o=document.createElement("style"),n=dt.litNonce;n!==void 0&&o.setAttribute("nonce",n),o.textContent=i.cssText,e.appendChild(o)}},_t=ht?e=>e:e=>e instanceof CSSStyleSheet?(t=>{let i="";for(let o of t.cssRules)i+=o.cssText;return ee(i)})(e):e;/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var{is:ni,defineProperty:si,getOwnPropertyDescriptor:ai,getOwnPropertyNames:ri,getOwnPropertySymbols:li,getPrototypeOf:ci}=Object,I=globalThis,oe=I.trustedTypes,di=oe?oe.emptyScript:"",hi=I.reactiveElementPolyfillSupport,Z=(e,t)=>e,J={toAttribute(e,t){switch(t){case Boolean:e=e?di:null;break;case Object:case Array:e=e==null?e:JSON.stringify(e)}return e},fromAttribute(e,t){let i=e;switch(t){case Boolean:i=e!==null;break;case Number:i=e===null?null:Number(e);break;case Object:case Array:try{i=JSON.parse(e)}catch{i=null}}return i}},mt=(e,t)=>!ni(e,t),ne={attribute:!0,type:String,converter:J,reflect:!1,useDefault:!1,hasChanged:mt};Symbol.metadata??(Symbol.metadata=Symbol("metadata")),I.litPropertyMetadata??(I.litPropertyMetadata=new WeakMap);var E=class extends HTMLElement{static addInitializer(t){this._$Ei(),(this.l??(this.l=[])).push(t)}static get observedAttributes(){return this.finalize(),this._$Eh&&[...this._$Eh.keys()]}static createProperty(t,i=ne){if(i.state&&(i.attribute=!1),this._$Ei(),this.prototype.hasOwnProperty(t)&&((i=Object.create(i)).wrapped=!0),this.elementProperties.set(t,i),!i.noAccessor){let o=Symbol(),n=this.getPropertyDescriptor(t,o,i);n!==void 0&&si(this.prototype,t,n)}}static getPropertyDescriptor(t,i,o){let{get:n,set:s}=ai(this.prototype,t)??{get(){return this[i]},set(a){this[i]=a}};return{get:n,set(a){let l=n?.call(this);s?.call(this,a),this.requestUpdate(t,l,o)},configurable:!0,enumerable:!0}}static getPropertyOptions(t){return this.elementProperties.get(t)??ne}static _$Ei(){if(this.hasOwnProperty(Z("elementProperties")))return;let t=ci(this);t.finalize(),t.l!==void 0&&(this.l=[...t.l]),this.elementProperties=new Map(t.elementProperties)}static finalize(){if(this.hasOwnProperty(Z("finalized")))return;if(this.finalized=!0,this._$Ei(),this.hasOwnProperty(Z("properties"))){let i=this.properties,o=[...ri(i),...li(i)];for(let n of o)this.createProperty(n,i[n])}let t=this[Symbol.metadata];if(t!==null){let i=litPropertyMetadata.get(t);if(i!==void 0)for(let[o,n]of i)this.elementProperties.set(o,n)}this._$Eh=new Map;for(let[i,o]of this.elementProperties){let n=this._$Eu(i,o);n!==void 0&&this._$Eh.set(n,i)}this.elementStyles=this.finalizeStyles(this.styles)}static finalizeStyles(t){let i=[];if(Array.isArray(t)){let o=new Set(t.flat(1/0).reverse());for(let n of o)i.unshift(_t(n))}else t!==void 0&&i.push(_t(t));return i}static _$Eu(t,i){let o=i.attribute;return o===!1?void 0:typeof o=="string"?o:typeof t=="string"?t.toLowerCase():void 0}constructor(){super(),this._$Ep=void 0,this.isUpdatePending=!1,this.hasUpdated=!1,this._$Em=null,this._$Ev()}_$Ev(){this._$ES=new Promise(t=>this.enableUpdating=t),this._$AL=new Map,this._$E_(),this.requestUpdate(),this.constructor.l?.forEach(t=>t(this))}addController(t){(this._$EO??(this._$EO=new Set)).add(t),this.renderRoot!==void 0&&this.isConnected&&t.hostConnected?.()}removeController(t){this._$EO?.delete(t)}_$E_(){let t=new Map,i=this.constructor.elementProperties;for(let o of i.keys())this.hasOwnProperty(o)&&(t.set(o,this[o]),delete this[o]);t.size>0&&(this._$Ep=t)}createRenderRoot(){let t=this.shadowRoot??this.attachShadow(this.constructor.shadowRootOptions);return ie(t,this.constructor.elementStyles),t}connectedCallback(){this.renderRoot??(this.renderRoot=this.createRenderRoot()),this.enableUpdating(!0),this._$EO?.forEach(t=>t.hostConnected?.())}enableUpdating(t){}disconnectedCallback(){this._$EO?.forEach(t=>t.hostDisconnected?.())}attributeChangedCallback(t,i,o){this._$AK(t,o)}_$ET(t,i){let o=this.constructor.elementProperties.get(t),n=this.constructor._$Eu(t,o);if(n!==void 0&&o.reflect===!0){let s=(o.converter?.toAttribute!==void 0?o.converter:J).toAttribute(i,o.type);this._$Em=t,s==null?this.removeAttribute(n):this.setAttribute(n,s),this._$Em=null}}_$AK(t,i){let o=this.constructor,n=o._$Eh.get(t);if(n!==void 0&&this._$Em!==n){let s=o.getPropertyOptions(n),a=typeof s.converter=="function"?{fromAttribute:s.converter}:s.converter?.fromAttribute!==void 0?s.converter:J;this._$Em=n;let l=a.fromAttribute(i,s.type);this[n]=l??this._$Ej?.get(n)??l,this._$Em=null}}requestUpdate(t,i,o,n=!1,s){if(t!==void 0){let a=this.constructor;if(n===!1&&(s=this[t]),o??(o=a.getPropertyOptions(t)),!((o.hasChanged??mt)(s,i)||o.useDefault&&o.reflect&&s===this._$Ej?.get(t)&&!this.hasAttribute(a._$Eu(t,o))))return;this.C(t,i,o)}this.isUpdatePending===!1&&(this._$ES=this._$EP())}C(t,i,{useDefault:o,reflect:n,wrapped:s},a){o&&!(this._$Ej??(this._$Ej=new Map)).has(t)&&(this._$Ej.set(t,a??i??this[t]),s!==!0||a!==void 0)||(this._$AL.has(t)||(this.hasUpdated||o||(i=void 0),this._$AL.set(t,i)),n===!0&&this._$Em!==t&&(this._$Eq??(this._$Eq=new Set)).add(t))}async _$EP(){this.isUpdatePending=!0;try{await this._$ES}catch(i){Promise.reject(i)}let t=this.scheduleUpdate();return t!=null&&await t,!this.isUpdatePending}scheduleUpdate(){return this.performUpdate()}performUpdate(){if(!this.isUpdatePending)return;if(!this.hasUpdated){if(this.renderRoot??(this.renderRoot=this.createRenderRoot()),this._$Ep){for(let[n,s]of this._$Ep)this[n]=s;this._$Ep=void 0}let o=this.constructor.elementProperties;if(o.size>0)for(let[n,s]of o){let{wrapped:a}=s,l=this[n];a!==!0||this._$AL.has(n)||l===void 0||this.C(n,void 0,s,l)}}let t=!1,i=this._$AL;try{t=this.shouldUpdate(i),t?(this.willUpdate(i),this._$EO?.forEach(o=>o.hostUpdate?.()),this.update(i)):this._$EM()}catch(o){throw t=!1,this._$EM(),o}t&&this._$AE(i)}willUpdate(t){}_$AE(t){this._$EO?.forEach(i=>i.hostUpdated?.()),this.hasUpdated||(this.hasUpdated=!0,this.firstUpdated(t)),this.updated(t)}_$EM(){this._$AL=new Map,this.isUpdatePending=!1}get updateComplete(){return this.getUpdateComplete()}getUpdateComplete(){return this._$ES}shouldUpdate(t){return!0}update(t){this._$Eq&&(this._$Eq=this._$Eq.forEach(i=>this._$ET(i,this[i]))),this._$EM()}updated(t){}firstUpdated(t){}};E.elementStyles=[],E.shadowRootOptions={mode:"open"},E[Z("elementProperties")]=new Map,E[Z("finalized")]=new Map,hi?.({ReactiveElement:E}),(I.reactiveElementVersions??(I.reactiveElementVersions=[])).push("2.1.2");/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var tt=globalThis,se=e=>e,ut=tt.trustedTypes,ae=ut?ut.createPolicy("lit-html",{createHTML:e=>e}):void 0,St="$lit$",S=`lit$${Math.random().toFixed(9).slice(2)}$`,kt="?"+S,mi=`<${kt}>`,O=document,et=()=>O.createComment(""),it=e=>e===null||typeof e!="object"&&typeof e!="function",Mt=Array.isArray,me=e=>Mt(e)||typeof e?.[Symbol.iterator]=="function",Et=`[ 	
\f\r]`,Q=/<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g,re=/-->/g,le=/>/g,U=RegExp(`>|${Et}(?:([^\\s"'>=/]+)(${Et}*=${Et}*(?:[^ 	
\f\r"'\`<>=]|("|')|))|$)`,"g"),ce=/'/g,de=/"/g,ue=/^(?:script|style|textarea|title)$/i,Ft=e=>(t,...i)=>({_$litType$:e,strings:t,values:i}),y=Ft(1),Ji=Ft(2),Qi=Ft(3),C=Symbol.for("lit-noChange"),p=Symbol.for("lit-nothing"),he=new WeakMap,B=O.createTreeWalker(O,129);function pe(e,t){if(!Mt(e)||!e.hasOwnProperty("raw"))throw Error("invalid template strings array");return ae!==void 0?ae.createHTML(t):t}var fe=(e,t)=>{let i=e.length-1,o=[],n,s=t===2?"<svg>":t===3?"<math>":"",a=Q;for(let l=0;l<i;l++){let c=e[l],m,f,u=-1,_=0;for(;_<c.length&&(a.lastIndex=_,f=a.exec(c),f!==null);)_=a.lastIndex,a===Q?f[1]==="!--"?a=re:f[1]!==void 0?a=le:f[2]!==void 0?(ue.test(f[2])&&(n=RegExp("</"+f[2],"g")),a=U):f[3]!==void 0&&(a=U):a===U?f[0]===">"?(a=n??Q,u=-1):f[1]===void 0?u=-2:(u=a.lastIndex-f[2].length,m=f[1],a=f[3]===void 0?U:f[3]==='"'?de:ce):a===de||a===ce?a=U:a===re||a===le?a=Q:(a=U,n=void 0);let F=a===U&&e[l+1].startsWith("/>")?" ":"";s+=a===Q?c+mi:u>=0?(o.push(m),c.slice(0,u)+St+c.slice(u)+S+F):c+S+(u===-2?l:F)}return[pe(e,s+(e[i]||"<?>")+(t===2?"</svg>":t===3?"</math>":"")),o]},ot=class e{constructor({strings:t,_$litType$:i},o){let n;this.parts=[];let s=0,a=0,l=t.length-1,c=this.parts,[m,f]=fe(t,i);if(this.el=e.createElement(m,o),B.currentNode=this.el.content,i===2||i===3){let u=this.el.content.firstChild;u.replaceWith(...u.childNodes)}for(;(n=B.nextNode())!==null&&c.length<l;){if(n.nodeType===1){if(n.hasAttributes())for(let u of n.getAttributeNames())if(u.endsWith(St)){let _=f[a++],F=n.getAttribute(u).split(S),ct=/([.?@])?(.*)/.exec(_);c.push({type:1,index:s,name:ct[2],strings:F,ctor:ct[1]==="."?ft:ct[1]==="?"?gt:ct[1]==="@"?vt:D}),n.removeAttribute(u)}else u.startsWith(S)&&(c.push({type:6,index:s}),n.removeAttribute(u));if(ue.test(n.tagName)){let u=n.textContent.split(S),_=u.length-1;if(_>0){n.textContent=ut?ut.emptyScript:"";for(let F=0;F<_;F++)n.append(u[F],et()),B.nextNode(),c.push({type:2,index:++s});n.append(u[_],et())}}}else if(n.nodeType===8)if(n.data===kt)c.push({type:2,index:s});else{let u=-1;for(;(u=n.data.indexOf(S,u+1))!==-1;)c.push({type:7,index:s}),u+=S.length-1}s++}}static createElement(t,i){let o=O.createElement("template");return o.innerHTML=t,o}};function q(e,t,i=e,o){if(t===C)return t;let n=o!==void 0?i._$Co?.[o]:i._$Cl,s=it(t)?void 0:t._$litDirective$;return n?.constructor!==s&&(n?._$AO?.(!1),s===void 0?n=void 0:(n=new s(e),n._$AT(e,i,o)),o!==void 0?(i._$Co??(i._$Co=[]))[o]=n:i._$Cl=n),n!==void 0&&(t=q(e,n._$AS(e,t.values),n,o)),t}var pt=class{constructor(t,i){this._$AV=[],this._$AN=void 0,this._$AD=t,this._$AM=i}get parentNode(){return this._$AM.parentNode}get _$AU(){return this._$AM._$AU}u(t){let{el:{content:i},parts:o}=this._$AD,n=(t?.creationScope??O).importNode(i,!0);B.currentNode=n;let s=B.nextNode(),a=0,l=0,c=o[0];for(;c!==void 0;){if(a===c.index){let m;c.type===2?m=new j(s,s.nextSibling,this,t):c.type===1?m=new c.ctor(s,c.name,c.strings,this,t):c.type===6&&(m=new bt(s,this,t)),this._$AV.push(m),c=o[++l]}a!==c?.index&&(s=B.nextNode(),a++)}return B.currentNode=O,n}p(t){let i=0;for(let o of this._$AV)o!==void 0&&(o.strings!==void 0?(o._$AI(t,o,i),i+=o.strings.length-2):o._$AI(t[i])),i++}},j=class e{get _$AU(){return this._$AM?._$AU??this._$Cv}constructor(t,i,o,n){this.type=2,this._$AH=p,this._$AN=void 0,this._$AA=t,this._$AB=i,this._$AM=o,this.options=n,this._$Cv=n?.isConnected??!0}get parentNode(){let t=this._$AA.parentNode,i=this._$AM;return i!==void 0&&t?.nodeType===11&&(t=i.parentNode),t}get startNode(){return this._$AA}get endNode(){return this._$AB}_$AI(t,i=this){t=q(this,t,i),it(t)?t===p||t==null||t===""?(this._$AH!==p&&this._$AR(),this._$AH=p):t!==this._$AH&&t!==C&&this._(t):t._$litType$!==void 0?this.$(t):t.nodeType!==void 0?this.T(t):me(t)?this.k(t):this._(t)}O(t){return this._$AA.parentNode.insertBefore(t,this._$AB)}T(t){this._$AH!==t&&(this._$AR(),this._$AH=this.O(t))}_(t){this._$AH!==p&&it(this._$AH)?this._$AA.nextSibling.data=t:this.T(O.createTextNode(t)),this._$AH=t}$(t){let{values:i,_$litType$:o}=t,n=typeof o=="number"?this._$AC(t):(o.el===void 0&&(o.el=ot.createElement(pe(o.h,o.h[0]),this.options)),o);if(this._$AH?._$AD===n)this._$AH.p(i);else{let s=new pt(n,this),a=s.u(this.options);s.p(i),this.T(a),this._$AH=s}}_$AC(t){let i=he.get(t.strings);return i===void 0&&he.set(t.strings,i=new ot(t)),i}k(t){Mt(this._$AH)||(this._$AH=[],this._$AR());let i=this._$AH,o,n=0;for(let s of t)n===i.length?i.push(o=new e(this.O(et()),this.O(et()),this,this.options)):o=i[n],o._$AI(s),n++;n<i.length&&(this._$AR(o&&o._$AB.nextSibling,n),i.length=n)}_$AR(t=this._$AA.nextSibling,i){for(this._$AP?.(!1,!0,i);t!==this._$AB;){let o=se(t).nextSibling;se(t).remove(),t=o}}setConnected(t){this._$AM===void 0&&(this._$Cv=t,this._$AP?.(t))}},D=class{get tagName(){return this.element.tagName}get _$AU(){return this._$AM._$AU}constructor(t,i,o,n,s){this.type=1,this._$AH=p,this._$AN=void 0,this.element=t,this.name=i,this._$AM=n,this.options=s,o.length>2||o[0]!==""||o[1]!==""?(this._$AH=Array(o.length-1).fill(new String),this.strings=o):this._$AH=p}_$AI(t,i=this,o,n){let s=this.strings,a=!1;if(s===void 0)t=q(this,t,i,0),a=!it(t)||t!==this._$AH&&t!==C,a&&(this._$AH=t);else{let l=t,c,m;for(t=s[0],c=0;c<s.length-1;c++)m=q(this,l[o+c],i,c),m===C&&(m=this._$AH[c]),a||(a=!it(m)||m!==this._$AH[c]),m===p?t=p:t!==p&&(t+=(m??"")+s[c+1]),this._$AH[c]=m}a&&!n&&this.j(t)}j(t){t===p?this.element.removeAttribute(this.name):this.element.setAttribute(this.name,t??"")}},ft=class extends D{constructor(){super(...arguments),this.type=3}j(t){this.element[this.name]=t===p?void 0:t}},gt=class extends D{constructor(){super(...arguments),this.type=4}j(t){this.element.toggleAttribute(this.name,!!t&&t!==p)}},vt=class extends D{constructor(t,i,o,n,s){super(t,i,o,n,s),this.type=5}_$AI(t,i=this){if((t=q(this,t,i,0)??p)===C)return;let o=this._$AH,n=t===p&&o!==p||t.capture!==o.capture||t.once!==o.once||t.passive!==o.passive,s=t!==p&&(o===p||n);n&&this.element.removeEventListener(this.name,this,o),s&&this.element.addEventListener(this.name,this,t),this._$AH=t}handleEvent(t){typeof this._$AH=="function"?this._$AH.call(this.options?.host??this.element,t):this._$AH.handleEvent(t)}},bt=class{constructor(t,i,o){this.element=t,this.type=6,this._$AN=void 0,this._$AM=i,this.options=o}get _$AU(){return this._$AM._$AU}_$AI(t){q(this,t)}},ge={M:St,P:S,A:kt,C:1,L:fe,R:pt,D:me,V:q,I:j,H:D,N:gt,U:vt,B:ft,F:bt},ui=tt.litHtmlPolyfillSupport;ui?.(ot,j),(tt.litHtmlVersions??(tt.litHtmlVersions=[])).push("3.3.3");var ve=(e,t,i)=>{let o=i?.renderBefore??t,n=o._$litPart$;if(n===void 0){let s=i?.renderBefore??null;o._$litPart$=n=new j(t.insertBefore(et(),s),s,void 0,i??{})}return n._$AI(e),n};/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var nt=globalThis,R=class extends E{constructor(){super(...arguments),this.renderOptions={host:this},this._$Do=void 0}createRenderRoot(){var i;let t=super.createRenderRoot();return(i=this.renderOptions).renderBefore??(i.renderBefore=t.firstChild),t}update(t){let i=this.render();this.hasUpdated||(this.renderOptions.isConnected=this.isConnected),super.update(t),this._$Do=ve(i,this.renderRoot,this.renderOptions)}connectedCallback(){super.connectedCallback(),this._$Do?.setConnected(!0)}disconnectedCallback(){super.disconnectedCallback(),this._$Do?.setConnected(!1)}render(){return C}};R._$litElement$=!0,R.finalized=!0,nt.litElementHydrateSupport?.({LitElement:R});var pi=nt.litElementPolyfillSupport;pi?.({LitElement:R});(nt.litElementVersions??(nt.litElementVersions=[])).push("4.2.2");/**
 * @license
 * Copyright 2022 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 *//*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var be=w`
  :host {
    /* Private - set by the component to control indentation depth */
    --indent: 0px;
    --show-duration: var(--wa-transition-normal);
    --hide-duration: var(--wa-transition-normal);

    display: block;
    color: var(--wa-color-text-normal);
    outline: 0;
    z-index: 0;
  }

  :host(:focus) {
    outline: none;
  }

  slot:not([name])::slotted(wa-icon) {
    margin-inline-end: 0.5em;
  }

  .tree-item {
    position: relative;
    display: flex;
    align-items: stretch;
    flex-direction: column;
    cursor: default;
    user-select: none;
    -webkit-user-select: none;
  }

  .checkbox {
    line-height: var(--wa-form-control-value-line-height);
    pointer-events: none;
  }

  .expand-button,
  .checkbox,
  .label {
    font-family: inherit;
    font-size: inherit;
    font-weight: inherit;
  }

  .checkbox::part(base) {
    display: flex;
    align-items: center;
  }

  .indentation {
    display: block;
    width: var(--indent);
    flex-shrink: 0;
  }

  .expand-button {
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--wa-color-text-quiet);
    width: 2em;
    height: 2em;
    flex-shrink: 0;
    cursor: pointer;
  }

  .expand-button {
    transition: rotate var(--wa-transition-normal) var(--wa-transition-easing);
  }

  .tree-item-expanded .expand-button {
    rotate: 90deg;
  }

  .tree-item-expanded:dir(rtl) .expand-button {
    rotate: -90deg;
  }

  .tree-item-expanded:not(.tree-item-loading) slot[name='expand-icon'],
  .tree-item:not(.tree-item-expanded) slot[name='collapse-icon'] {
    display: none;
  }

  .tree-item:not(.tree-item-has-expand-button):not(.tree-item-loading) .expand-icon-slot {
    display: none;
  }

  .tree-item:not(.tree-item-has-expand-button):not(.tree-item-loading) .expand-button {
    cursor: default;
  }

  .tree-item-loading .expand-icon-slot wa-icon {
    display: none;
  }

  .expand-button-visible {
    cursor: pointer;
  }

  .item {
    display: flex;
    align-items: center;
    border-inline-start: solid 0.1875em transparent;
  }

  :host([disabled]) .item {
    opacity: 0.5;
    outline: none;
    cursor: not-allowed;
  }

  :host(:focus-visible) .item {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
    z-index: 2;
  }

  :host(:not([aria-disabled='true'])) .tree-item-selected .item {
    background-color: var(--wa-color-neutral-fill-quiet);
    border-inline-start-color: var(--wa-color-brand-fill-loud);
  }

  :host(:not([aria-disabled='true'])) .expand-button {
    color: var(--wa-color-text-quiet);
  }

  .label {
    display: flex;
    align-items: center;
    transition: color var(--wa-transition-normal) var(--wa-transition-easing);
  }

  .children {
    display: block;
  }

  /* Indentation lines */
  .children {
    position: relative;
  }

  .children::before {
    content: '';
    position: absolute;
    top: var(--indent-guide-offset);
    bottom: var(--indent-guide-offset);
    inset-inline-start: calc(0.1875em + var(--indent) + 1em - (var(--indent-guide-width) / 2));
    border-inline-end: var(--indent-guide-width) var(--indent-guide-style) var(--indent-guide-color);
    z-index: 1;
  }

  @media (forced-colors: active) {
    :host(:not([aria-disabled='true'])) .tree-item-selected .item {
      outline: dashed 1px SelectedItem;
    }
  }
`;/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */async function It(e,t,i){return e.animate(t,i).finished.catch(()=>{})}function Rt(e){return e=e.toString().toLowerCase(),e.indexOf("ms")>-1?parseFloat(e)||0:e.indexOf("s")>-1?(parseFloat(e)||0)*1e3:parseFloat(e)||0}/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */function g(e,t){let i={waitUntilFirstUpdate:!1,...t};return(o,n)=>{let{update:s}=o,a=Array.isArray(e)?e:[e];o.update=function(l){a.forEach(c=>{let m=c;if(l.has(m)){let f=l.get(m),u=this[m];f!==u&&(!i.waitUntilFirstUpdate||this.hasUpdated)&&this[n](f,u)}}),s.call(this,l)}}}/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var fi=Object.defineProperty,gi=Object.getOwnPropertyDescriptor,we=e=>{throw TypeError(e)},r=(e,t,i,o)=>{for(var n=o>1?void 0:o?gi(t,i):t,s=e.length-1,a;s>=0;s--)(a=e[s])&&(n=(o?a(t,i,n):a(n))||n);return o&&n&&fi(t,i,n),n},Ce=(e,t,i)=>t.has(e)||we("Cannot "+i),ye=(e,t,i)=>(Ce(e,t,"read from private field"),i?i.call(e):t.get(e)),xe=(e,t,i)=>t.has(e)?we("Cannot add the same private member more than once"):t instanceof WeakSet?t.add(e):t.set(e,i),Le=(e,t,i,o)=>(Ce(e,t,"write to private field"),o?o.call(e,i):t.set(e,i),i);/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var z=e=>(t,i)=>{i!==void 0?i.addInitializer(()=>{customElements.define(e,t)}):customElements.define(e,t)};/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var vi={attribute:!0,type:String,converter:J,reflect:!1,hasChanged:mt},bi=(e=vi,t,i)=>{let{kind:o,metadata:n}=i,s=globalThis.litPropertyMetadata.get(n);if(s===void 0&&globalThis.litPropertyMetadata.set(n,s=new Map),o==="setter"&&((e=Object.create(e)).wrapped=!0),s.set(i.name,e),o==="accessor"){let{name:a}=i;return{set(l){let c=t.get.call(this);t.set.call(this,l),this.requestUpdate(a,c,e,!0,l)},init(l){return l!==void 0&&this.C(a,void 0,e,l),l}}}if(o==="setter"){let{name:a}=i;return function(l){let c=this[a];t.call(this,l),this.requestUpdate(a,c,e,!0,l)}}throw Error("Unsupported decorator location: "+o)};function d(e){return(t,i)=>typeof i=="object"?bi(e,t,i):((o,n,s)=>{let a=n.hasOwnProperty(s);return n.constructor.createProperty(s,o),a?Object.getOwnPropertyDescriptor(n,s):void 0})(e,t,i)}/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */function V(e){return d({...e,state:!0,attribute:!1})}/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 *//**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var N=(e,t,i)=>(i.configurable=!0,i.enumerable=!0,Reflect.decorate&&typeof t!="object"&&Object.defineProperty(e,t,i),i);/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */function L(e,t){return(i,o,n)=>{let s=a=>a.renderRoot?.querySelector(e)??null;if(t){let{get:a,set:l}=typeof o=="object"?i:n??(()=>{let c=Symbol();return{get(){return this[c]},set(m){this[c]=m}}})();return N(i,o,{get(){let c=a.call(this);return c===void 0&&(c=s(this),(c!==null||this.hasUpdated)&&l.call(this,c)),c}})}return N(i,o,{get(){return s(this)}})}}/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 *//**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 *//**
 * @license
 * Copyright 2021 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 *//**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 *//*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var wi=w`
  :host {
    box-sizing: border-box;
  }

  :host *,
  :host *::before,
  :host *::after {
    box-sizing: inherit;
  }

  [hidden],
  :host([hidden]) {
    display: none !important;
  }
`,Ci=/;\s+$/;function yi(e){return e.replace(/[A-Z]/g,t=>`-${t.toLowerCase()}`)}function Ae(e){let{property:t,value:i,element:o}=e;if(i){let n=o.getAttribute("style")||"";n&&(n.match(Ci)||(n+=";"),n+=" ");let s=`${t}: ${i}`;return n.includes(s)?void 0:`${n}${s};`}return null}var wt,x=class extends R{constructor(){super(),xe(this,wt,!1),this.initialReflectedProperties=new Map,this.didSSR=!!this.shadowRoot,this.customStates={set:(t,i)=>{if(this.internals?.states)try{i?this.internals.states.add(t):this.internals.states.delete(t)}catch(o){if(String(o).includes("must start with '--'"))console.error("Your browser implements an outdated version of CustomStateSet. Consider using a polyfill");else throw o}},has:t=>{if(!this.internals?.states)return!1;try{return this.internals.states.has(t)}catch{return!1}}};try{this.internals=this.attachInternals()}catch{console.error("Element internals are not supported in your browser. Consider using a polyfill")}this.customStates.set("wa-defined",!0);let e=this.constructor;for(let[t,i]of e.elementProperties)i.default==="inherit"&&i.initial!==void 0&&typeof t=="string"&&this.customStates.set(`initial-${t}-${i.initial}`,!0)}static get styles(){let e=Array.isArray(this.css)?this.css:this.css?[this.css]:[];return[wi,...e]}connectedCallback(){super.connectedCallback(),this.didSSR||this.shadowRoot?.prepend(document.createComment(` Web Awesome: https://webawesome.com/docs/components/${this.localName.replace("wa-","")} `)),this.didSSR&&this.updateComplete.then(()=>{this.shadowRoot?.prepend(document.createComment(` Web Awesome: https://webawesome.com/docs/components/${this.localName.replace("wa-","")} `))})}attributeChangedCallback(e,t,i){ye(this,wt)||(this.constructor.elementProperties.forEach((o,n)=>{o.reflect&&this[n]!=null&&this.initialReflectedProperties.set(n,this[n])}),Le(this,wt,!0)),super.attributeChangedCallback(e,t,i)}willUpdate(e){super.willUpdate(e),this.initialReflectedProperties.forEach((t,i)=>{e.has(i)&&this[i]==null&&(this[i]=t)})}firstUpdated(e){super.firstUpdated(e),this.didSSR&&this.shadowRoot?.querySelectorAll("slot").forEach(t=>{t.dispatchEvent(new Event("slotchange",{bubbles:!0,composed:!1,cancelable:!1}))})}update(e){try{super.update(e)}catch(t){if(this.didSSR&&!this.hasUpdated){let i=new Event("lit-hydration-error",{bubbles:!0,composed:!0,cancelable:!1});i.error=t,this.dispatchEvent(i)}throw t}}setStyle(e,t){if(!this.style){let i=Ae({property:yi(e),value:t,element:this});i&&this.setAttribute("style",i);return}this.style[e]=t}setStyleProperty(e,t){if(!this.style){let i=Ae({property:e,value:t,element:this});i&&this.setAttribute("style",i);return}this.style.setProperty(e,t)}relayNativeEvent(e,t){e.stopImmediatePropagation(),this.dispatchEvent(new e.constructor(e.type,{...e,...t}))}};wt=new WeakMap;r([d()],x.prototype,"dir",2);r([d()],x.prototype,"lang",2);r([d({type:Boolean,reflect:!0,attribute:"did-ssr"})],x.prototype,"didSSR",2);var Tt=new Set,H=new Map,k,Pt="ltr",Ut="en",ze=typeof MutationObserver<"u"&&typeof document<"u"&&typeof document.documentElement<"u";if(ze){let e=new MutationObserver($e);Pt=document.documentElement.dir||"ltr",Ut=document.documentElement.lang||navigator.language,e.observe(document.documentElement,{attributes:!0,attributeFilter:["dir","lang"]})}function st(...e){e.map(t=>{let i=t.$code.toLowerCase();H.has(i)?H.set(i,Object.assign(Object.assign({},H.get(i)),t)):H.set(i,t),k||(k=t)}),$e()}function $e(){ze&&(Pt=document.documentElement.dir||"ltr",Ut=document.documentElement.lang||navigator.language),[...Tt.keys()].map(e=>{typeof e.requestUpdate=="function"&&e.requestUpdate()})}var Ct=class{constructor(t){this.host=t,this.host.addController(this)}hostConnected(){Tt.add(this.host)}hostDisconnected(){Tt.delete(this.host)}dir(){return`${this.host.dir||Pt}`.toLowerCase()}lang(){let t=`${this.host.lang||Ut}`.toLowerCase().replace(/_/g,"-");try{return new Intl.Locale(t),t}catch{return k?k.$code.toLowerCase():"en"}}getTranslationData(t){var i,o;let n;try{n=new Intl.Locale(t.replace(/_/g,"-"))}catch{return{locale:void 0,language:"",region:"",primary:void 0,secondary:void 0}}let s=n.language.toLowerCase(),a=(o=(i=n.region)===null||i===void 0?void 0:i.toLowerCase())!==null&&o!==void 0?o:"",l=H.get(`${s}-${a}`),c=H.get(s);return{locale:n,language:s,region:a,primary:l,secondary:c}}exists(t,i){var o;let{primary:n,secondary:s}=this.getTranslationData((o=i.lang)!==null&&o!==void 0?o:this.lang());return i=Object.assign({includeFallback:!1},i),!!(n&&n[t]||s&&s[t]||i.includeFallback&&k&&k[t])}term(t,...i){let{primary:o,secondary:n}=this.getTranslationData(this.lang()),s;if(o&&o[t])s=o[t];else if(n&&n[t])s=n[t];else if(k&&k[t])s=k[t];else return console.error(`No translation found for: ${String(t)}`),String(t);return typeof s=="function"?s(...i):s}date(t,i){return t=new Date(t),new Intl.DateTimeFormat(this.lang(),i).format(t)}number(t,i){return t=Number(t),isNaN(t)?"":new Intl.NumberFormat(this.lang(),i).format(t)}relativeTime(t,i,o){return new Intl.RelativeTimeFormat(this.lang(),o).format(t,i)}};/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var _e={$code:"en",$name:"English",$dir:"ltr",am:"AM",autosizeColumn:"Autosize column",captions:"Captions",carousel:"Carousel",chooseDate:"Choose date",chooseDecade:"Choose decade",chooseMonth:"Choose month",chooseTime:"Choose time",chooseYear:"Choose year",clearEntry:"Clear entry",clearFilter:"Clear filter",clearSort:"Clear sort",close:"Close",closeCalendar:"Close calendar",closeTimeInput:"Close time picker",collapseRow:"Collapse row",columnMenu:"Column options",columnMovedToPosition:(e,t,i)=>`${e} moved to position ${t} of ${i}`,columns:"Columns",compactPageXOfY:(e,t)=>`${e} of ${t}`,copied:"Copied",copy:"Copy",createOption:e=>`Create "${e}"`,currentlyPlaying:"currently playing",currentValue:"Current value",date:"Date",datePickerKeyboardHelp:"Use arrow keys to change values; press Alt+Down Arrow to open the calendar.",day:"Day",dayPeriod:"AM/PM",decrement:"Decrement",deselectAllRows:"Deselect all rows",dropFileHere:"Drop file here or click to browse",dropFilesHere:"Drop files here or click to browse",empty:"Empty",endDate:"End date",enterFullscreen:"Enter fullscreen",error:"Error",exitFullscreen:"Exit fullscreen",expandRow:"Expand row",filterByColumn:e=>`Filter by ${e}`,filterFrom:"From",filterMax:"Max",filterMin:"Min",filterTo:"To",firstPage:"First page",goToSlide:(e,t)=>`Go to slide ${e} of ${t}`,hideColumn:"Hide column",hidePassword:"Hide password",hour:"Hour",incompleteDate:"Enter a valid date.",increment:"Increment",jumpBackwardX:e=>`Jump back ${e} pages`,jumpForwardX:e=>`Jump forward ${e} pages`,lastPage:"Last page",loading:"Loading",minute:"Minute",month:"Month",moreOptions:"More Options",mute:"Mute",nextDecade:"Next decade",nextMonth:"Next month",nextPage:"Next page",nextSlide:"Next slide",nextVideo:"Next Video",nextYear:"Next year",noData:"No data",noResults:"No matching results",now:"Now",numCharacters:e=>e===1?"1 character":`${e} characters`,numCharactersRemaining:e=>e===1?"1 character remaining":`${e} characters remaining`,numOptionsSelected:e=>e===0?"No options selected":e===1?"1 option selected":`${e} options selected`,numRowsCopied:e=>e===1?"1 row copied":`${e} rows copied`,numRowsSelected:e=>e===1?"1 row selected":`${e} rows selected`,pageXOfY:(e,t)=>`Page ${e} of ${t}`,pagination:"Pagination",pause:"Pause",pauseAnimation:"Pause animation",pictureInPicture:"Picture in picture",pinLeft:"Pin left",pinRight:"Pin right",play:"Play",playAnimation:"Play animation",playbackSpeed:"Playback speed",playlist:"Playlist",pm:"PM",previousDecade:"Previous decade",previousMonth:"Previous month",previousPage:"Previous page",previousSlide:"Previous slide",previousVideo:"Previous video",previousYear:"Previous year",progress:"Progress",rangeTooLong:e=>e===1?"Select a range no longer than 1 day":`Select a range no longer than ${e} days`,rangeTooShort:e=>e===1?"Select a range at least 1 day long":`Select a range at least ${e} days long`,readonly:"Read-only",remove:"Remove",resetColumns:"Reset columns",resize:"Resize",resizeColumn:"Resize column",rowsPerPage:"Rows per page",scrollableRegion:"Scrollable region",scrollToEnd:"Scroll to end",scrollToStart:"Scroll to start",search:"Search",second:"Second",seek:"Seek",seekProgress:(e,t)=>`${e} of ${t}`,selectAColorFromTheScreen:"Select a color from the screen",selectAllRows:"Select all rows",selected:"Selected",selectedDateLabel:e=>`Selected: ${e}`,selectedRangeLabel:e=>`Selected range: ${e}`,selectGroup:"Select group",selectionCleared:"Selection cleared",selectRow:"Select row",showingNofMRows:(e,t)=>`Showing ${e} of ${t} rows`,showingXtoYofZ:(e,t,i)=>`${e}\u2013${t} of ${i}`,showPassword:"Show password",slideNum:e=>`Slide ${e}`,sortAscending:"Sort ascending",sortColumn:"Sort column",sortDescending:"Sort descending",startDate:"Start date",time:"Time",timeInputKeyboardHelp:"Use arrow keys to change values; press Alt+Down Arrow to open the time picker.",today:"Today",toggleColorFormat:"Toggle color format",unmute:"Unmute",unpin:"Unpin",unpinColumn:"Unpin column",videoPlayer:"Video player",volume:"Volume",year:"Year",zoomIn:"Zoom in",zoomOut:"Zoom out"};st(_e);var Ee=_e;/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var W=class extends Ct{lang(){return this.host.didSSR&&!this.host.hasUpdated?this.host.lang||"en":super.lang()}};st(Ee);/**
 * @license
 * Copyright 2021 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var P=class extends Event{constructor(t,i,o,n){super("context-request",{bubbles:!0,composed:!0}),this.context=t,this.contextTarget=i,this.callback=o,this.subscribe=n??!1}};/**
 * @license
 * Copyright 2021 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 *//**
 * @license
 * Copyright 2021 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var X=class{constructor(t,i,o,n){if(this.subscribe=!1,this.provided=!1,this.value=void 0,this.t=(s,a)=>{this.unsubscribe&&(this.unsubscribe!==a&&(this.provided=!1,this.unsubscribe()),this.subscribe||this.unsubscribe()),this.value=s,this.host.requestUpdate(),this.provided&&!this.subscribe||(this.provided=!0,this.callback&&this.callback(s,a)),this.unsubscribe=a},this.host=t,i.context!==void 0){let s=i;this.context=s.context,this.callback=s.callback,this.subscribe=s.subscribe??!1}else this.context=i,this.callback=o,this.subscribe=n??!1;this.host.addController(this)}hostConnected(){this.dispatchRequest()}hostDisconnected(){this.unsubscribe&&(this.unsubscribe(),this.unsubscribe=void 0)}dispatchRequest(){this.host.dispatchEvent(new P(this.context,this.host,this.t,this.subscribe))}};/**
 * @license
 * Copyright 2021 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var yt=class{get value(){return this.o}set value(t){this.setValue(t)}setValue(t,i=!1){let o=i||!Object.is(t,this.o);this.o=t,o&&this.updateObservers()}constructor(t){this.subscriptions=new Map,this.updateObservers=()=>{for(let[i,{disposer:o}]of this.subscriptions)i(this.o,o)},t!==void 0&&(this.value=t)}addCallback(t,i,o){if(!o)return void t(this.value);this.subscriptions.has(t)||this.subscriptions.set(t,{disposer:()=>{this.subscriptions.delete(t)},consumerHost:i});let{disposer:n}=this.subscriptions.get(t);t(this.value,n)}clearCallbacks(){this.subscriptions.clear()}};/**
 * @license
 * Copyright 2021 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var Bt=class extends Event{constructor(t,i){super("context-provider",{bubbles:!0,composed:!0}),this.context=t,this.contextTarget=i}},Y=class extends yt{constructor(t,i,o){super(i.context!==void 0?i.initialValue:o),this.onContextRequest=n=>{if(n.context!==this.context)return;let s=n.contextTarget??n.composedPath()[0];s!==this.host&&(n.stopPropagation(),this.addCallback(n.callback,s,n.subscribe))},this.onProviderRequest=n=>{if(n.context!==this.context||(n.contextTarget??n.composedPath()[0])===this.host)return;let s=new Set;for(let[a,{consumerHost:l}]of this.subscriptions)s.has(a)||(s.add(a),l.dispatchEvent(new P(this.context,l,a,!0)));n.stopPropagation()},this.host=t,i.context!==void 0?this.context=i.context:this.context=i,this.attachListeners(),this.host.addController?.(this)}attachListeners(){this.host.addEventListener("context-request",this.onContextRequest),this.host.addEventListener("context-provider",this.onProviderRequest)}hostConnected(){this.host.dispatchEvent(new Bt(this.context,this.host))}};/**
 * @license
 * Copyright 2021 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 *//**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */function Ot({context:e}){return(t,i)=>{let o=new WeakMap;if(typeof i=="object")return{get(){return t.get.call(this)},set(n){return o.get(this).setValue(n),t.set.call(this,n)},init(n){return o.set(this,new Y(this,{context:e,initialValue:n})),n}};{t.constructor.addInitializer((a=>{o.set(a,new Y(a,{context:e}))}));let n=Object.getOwnPropertyDescriptor(t,i),s;if(n===void 0){let a=new WeakMap;s={get(){return a.get(this)},set(l){o.get(this).setValue(l),a.set(this,l)},configurable:!0,enumerable:!0}}else{let a=n.set;s={...n,set(l){o.get(this).setValue(l),a?.call(this,l)}}}return void Object.defineProperty(t,i,s)}}}/**
 * @license
 * Copyright 2022 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */function qt({context:e,subscribe:t}){return(i,o)=>{typeof o=="object"?o.addInitializer((function(){new X(this,{context:e,callback:n=>{i.set.call(this,n)},subscribe:t})})):i.constructor.addInitializer((n=>{new X(n,{context:e,callback:s=>{n[o]=s},subscribe:t})}))}}/**
 * @license
 * Copyright 2017 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var M={ATTRIBUTE:1,CHILD:2,PROPERTY:3,BOOLEAN_ATTRIBUTE:4,EVENT:5,ELEMENT:6},xt=e=>(...t)=>({_$litDirective$:e,values:t}),K=class{constructor(t){}get _$AU(){return this._$AM._$AU}_$AT(t,i,o){this._$Ct=t,this._$AM=i,this._$Ci=o}_$AS(t,i){return this.update(t,i)}update(t,i){return this.render(...i)}};/**
 * @license
 * Copyright 2018 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var at=xt(class extends K{constructor(e){if(super(e),e.type!==M.ATTRIBUTE||e.name!=="class"||e.strings?.length>2)throw Error("`classMap()` can only be used in the `class` attribute and must be the only part in the attribute.")}render(e){return" "+Object.keys(e).filter(t=>e[t]).join(" ")+" "}update(e,[t]){if(this.st===void 0){this.st=new Set,e.strings!==void 0&&(this.nt=new Set(e.strings.join(" ").split(/\s/).filter(o=>o!=="")));for(let o in t)t[o]&&!this.nt?.has(o)&&this.st.add(o);return this.render(t)}let i=e.element.classList;for(let o of this.st)o in t||(i.remove(o),this.st.delete(o));for(let o in t){let n=!!t[o];n===this.st.has(o)||this.nt?.has(o)||(n?(i.add(o),this.st.add(o)):(i.remove(o),this.st.delete(o)))}return C}});/**
 * @license
 * Copyright 2020 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var{I:Un}=ge;var Se=(e,t)=>t===void 0?e?._$litType$!==void 0:e?._$litType$===t;var ke=e=>e.strings===void 0;var Li={},Me=(e,t=Li)=>e._$AH=t;/**
 * @license
 * Copyright 2020 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var rt=xt(class extends K{constructor(e){if(super(e),e.type!==M.PROPERTY&&e.type!==M.ATTRIBUTE&&e.type!==M.BOOLEAN_ATTRIBUTE)throw Error("The `live` directive is not allowed on child or event bindings");if(!ke(e))throw Error("`live` bindings can only contain a single expression")}render(e){return e}update(e,[t]){if(t===C||t===p)return t;let i=e.element,o=e.name;if(e.type===M.PROPERTY){if(t===i[o])return C}else if(e.type===M.BOOLEAN_ATTRIBUTE){if(!!t===i.hasAttribute(o))return C}else if(e.type===M.ATTRIBUTE&&i.getAttribute(o)===t+"")return C;return Me(e),t}});/**
 * @license
 * Copyright 2021 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */function Dt(e,t,i){return e?t(e):i?.(e)}/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Fe="wa-tree-item",h=class extends x{constructor(){super(...arguments),this.localize=new W(this),this.indeterminate=!1,this.isLeaf=!1,this.loading=!1,this.selectable=!1,this.expanded=!1,this.selected=!1,this.disabled=!1,this.lazy=!1,this._treeItemContext={depth:0,expanded:this.expanded},this._parentTreeContext=null,this.animationGeneration=0,this.tabIndex=-1,this.role="treeitem"}static isTreeItem(e){let t=e;return t&&(t.role==="treeitem"||t.getAttribute?.("role")==="treeitem")}connectedCallback(){super.connectedCallback(),this.setAttribute("role","treeitem"),this.setAttribute("tabIndex",this.tabIndex.toString()),this.isNestedItem()&&(this.setAttribute("slot","children"),this._parentTreeContext?.expanded||(this.expanded=!1)),this._parentTreeContext&&(this._treeItemContext={depth:this._parentTreeContext.depth+1,expanded:this.expanded}),this.updateIndentation()}firstUpdated(){this.childrenContainer.hidden=!this.expanded,this.childrenContainer.style.height=this.expanded?"auto":"0",this.isLeaf=!this.lazy&&this.getChildrenItems().length===0,this.handleExpandedChange()}async animateCollapse(e){this.dispatchEvent(new Zt);let t=Rt(getComputedStyle(this.childrenContainer).getPropertyValue("--hide-duration"));await It(this.childrenContainer,[{height:`${this.childrenContainer.scrollHeight}px`,opacity:"1",overflow:"hidden"},{height:"0",opacity:"0",overflow:"hidden"}],{duration:t,easing:"cubic-bezier(0.4, 0.0, 0.2, 1)"}),this.animationGeneration===e&&(this.childrenContainer.hidden=!0,this.dispatchEvent(new Jt))}isNestedItem(){if(this._parentTreeContext!==null)return!0;let e=this.parentElement;return!!e&&h.isTreeItem(e)}updateIndentation(){let e=Math.max(this._treeItemContext?.depth||0,this.getDepth());this.setStyleProperty("--indent",`calc(${e} * var(--indent-size, 2em))`)}getDepth(){let e=0,t=this.parentElement;for(;t;)h.isTreeItem(t)&&e++,t=t.parentElement;return e}handleChildrenSlotChange(){this.loading=!1,this.isLeaf=!this.lazy&&this.getChildrenItems().length===0}willUpdate(e){e.has("selected")&&!e.has("indeterminate")&&(this.indeterminate=!1),super.willUpdate(e)}async animateExpand(e){this.dispatchEvent(new Gt),this.childrenContainer.hidden=!1;let t=Rt(getComputedStyle(this.childrenContainer).getPropertyValue("--show-duration"));await It(this.childrenContainer,[{height:"0",opacity:"0",overflow:"hidden"},{height:`${this.childrenContainer.scrollHeight}px`,opacity:"1",overflow:"hidden"}],{duration:t,easing:"cubic-bezier(0.4, 0.0, 0.2, 1)"}),this.animationGeneration===e&&(this.childrenContainer.style.height="auto",this.dispatchEvent(new Qt))}handleLoadingChange(){this.setAttribute("aria-busy",this.loading?"true":"false"),this.loading||this.animateExpand(this.animationGeneration)}handleDisabledChange(){this.customStates.set("disabled",this.disabled),this.setAttribute("aria-disabled",this.disabled?"true":"false")}handleExpandedState(){this.customStates.set("expanded",this.expanded)}handleIndeterminateStateChange(){this.customStates.set("indeterminate",this.indeterminate)}handleSelectedChange(){this.customStates.set("selected",this.selected),this.setAttribute("aria-selected",this.selected?"true":"false")}handleExpandedChange(){this.isLeaf?this.removeAttribute("aria-expanded"):this.setAttribute("aria-expanded",this.expanded?"true":"false")}handleExpandAnimation(){this.animationGeneration++;let e=this.animationGeneration;this.expanded?this.lazy?(this.loading=!0,this.dispatchEvent(new Kt)):this.animateExpand(e):this.animateCollapse(e)}handleLazyChange(){this.dispatchEvent(new Yt)}getChildrenItems({includeDisabled:e=!0}={}){return this.childrenSlot?[...this.childrenSlot.assignedElements({flatten:!0})].filter(t=>h.isTreeItem(t)&&(e||!t.disabled)):[]}render(){let e=this.localize.dir()==="rtl",t=!this.loading&&(!this.isLeaf||this.lazy);return y`
      <div
        part="base tree-item"
        class="${at({"tree-item":!0,"tree-item-expanded":this.expanded,"tree-item-selected":this.selected,"tree-item-leaf":this.isLeaf,"tree-item-loading":this.loading,"tree-item-has-expand-button":t})}"
      >
        <div class="item" part="item">
          <div class="indentation" part="indentation"></div>

          <div
            part="expand-button"
            class=${at({"expand-button":!0,"expand-button-visible":t})}
            aria-hidden="true"
          >
            <slot class="expand-icon-slot" name="expand-icon">
              ${Dt(this.loading,()=>y` <wa-spinner part="spinner" exportparts="base:spinner__base"></wa-spinner> `,()=>y`
                  <wa-icon name=${e?"chevron-left":"chevron-right"} library="system" variant="solid"></wa-icon>
                `)}
            </slot>
            <slot class="expand-icon-slot" name="collapse-icon">
              <wa-icon name=${e?"chevron-left":"chevron-right"} library="system" variant="solid"></wa-icon>
            </slot>
          </div>

          ${Dt(this.selectable,()=>y`
              <wa-checkbox
                part="checkbox"
                exportparts="
                    base:checkbox__base,
                    control:checkbox__control,
                    checked-icon:checkbox__checked-icon,
                    indeterminate-icon:checkbox__indeterminate-icon,
                    label:checkbox__label
                  "
                class="checkbox"
                ?disabled="${this.disabled}"
                ?checked="${rt(this.selected)}"
                ?indeterminate="${this.indeterminate}"
                tabindex="-1"
              ></wa-checkbox>
            `)}

          <slot class="label" part="label"></slot>
        </div>

        <div class="children" part="children" role="group" ?hidden=${!this.expanded&&!this.isConnected}>
          <slot name="children" @slotchange="${this.handleChildrenSlotChange}"></slot>
        </div>
      </div>
    `}};h.css=be;r([V()],h.prototype,"indeterminate",2);r([V()],h.prototype,"isLeaf",2);r([V()],h.prototype,"loading",2);r([V()],h.prototype,"selectable",2);r([d({type:Boolean,reflect:!0})],h.prototype,"expanded",2);r([d({type:Boolean,reflect:!0})],h.prototype,"selected",2);r([d({type:Boolean,reflect:!0})],h.prototype,"disabled",2);r([d({type:Boolean,reflect:!0})],h.prototype,"lazy",2);r([Ot({context:Fe})],h.prototype,"_treeItemContext",2);r([qt({context:Fe,subscribe:!1})],h.prototype,"_parentTreeContext",2);r([L("slot:not([name])")],h.prototype,"defaultSlot",2);r([L("slot[name=children]")],h.prototype,"childrenSlot",2);r([L(".item")],h.prototype,"itemElement",2);r([L(".children")],h.prototype,"childrenContainer",2);r([L(".expand-button slot")],h.prototype,"expandButtonSlot",2);r([d({reflect:!0,type:Number,attribute:"tabindex"})],h.prototype,"tabIndex",2);r([d({reflect:!0})],h.prototype,"role",2);r([g("loading",{waitUntilFirstUpdate:!0})],h.prototype,"handleLoadingChange",1);r([g("disabled")],h.prototype,"handleDisabledChange",1);r([g("expanded")],h.prototype,"handleExpandedState",1);r([g("indeterminate")],h.prototype,"handleIndeterminateStateChange",1);r([g("selected")],h.prototype,"handleSelectedChange",1);r([g("expanded",{waitUntilFirstUpdate:!0})],h.prototype,"handleExpandedChange",1);r([g("expanded",{waitUntilFirstUpdate:!0})],h.prototype,"handleExpandAnimation",1);r([g("lazy",{waitUntilFirstUpdate:!0})],h.prototype,"handleLazyChange",1);h=r([z("wa-tree-item")],h);h.disableWarning?.("change-in-update");/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Ie=w`
  :host {
    /*
     * These are actually used by tree item, but we define them here so they can more easily be set and all tree items
     * stay consistent.
     */
    --indent-guide-color: var(--wa-color-surface-border);
    --indent-guide-offset: 0;
    --indent-guide-style: solid;
    --indent-guide-width: 0;
    --indent-size: 2em;

    display: block;
  }
`;/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */function Re(e,t,i){let o=n=>Object.is(n,-0)?0:n;return e<t?o(t):e>i?o(i):o(e)}/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */function Te(e,t=!1){function i(s){let a=s.getChildrenItems({includeDisabled:!1});if(a.length){let l=a.every(m=>m.selected),c=a.every(m=>!m.selected&&!m.indeterminate);s.selected=l,s.indeterminate=!l&&!c}}function o(s){let a=s.parentElement;h.isTreeItem(a)&&(i(a),o(a))}function n(s){for(let a of s.getChildrenItems())a.selected=t?s.selected||a.selected:!a.disabled&&s.selected,n(a);t&&i(s)}n(e),o(e)}var A=class extends x{constructor(){super(),this.selection="single",this.clickTarget=null,this.localize=new W(this),this.tabIndex=0,this.role="tree",this.initTreeItem=e=>{e.updateComplete.then(()=>{e.selectable=this.selection==="multiple"||this.selection==="leaf-multiple"&&e.isLeaf,["expand","collapse"].filter(t=>!!this.querySelector(`[slot="${t}-icon"]`)).forEach(t=>{let i=e.querySelector(`[slot="${t}-icon"]`),o=this.getExpandButtonIcon(t);o&&(i===null?e.append(o):i.hasAttribute("data-default")&&i.replaceWith(o))})})},this.handleTreeChanged=e=>{for(let t of e){let i=[...t.addedNodes].filter(h.isTreeItem),o=[...t.removedNodes].filter(h.isTreeItem);i.forEach(this.initTreeItem),this.lastFocusedItem&&o.includes(this.lastFocusedItem)&&(this.lastFocusedItem=null)}},this.handleFocusOut=e=>{let t=e.relatedTarget;(!t||!this.contains(t))&&(this.tabIndex=0)},this.handleFocusIn=e=>{let t=e.target;e.target===this&&this.focusItem(this.lastFocusedItem||this.getAllTreeItems()[0]),h.isTreeItem(t)&&!t.disabled&&(this.lastFocusedItem&&(this.lastFocusedItem.tabIndex=-1),this.lastFocusedItem=t,this.tabIndex=-1,t.tabIndex=0)},"addEventListener"in this&&(this.addEventListener("focusin",this.handleFocusIn),this.addEventListener("focusout",this.handleFocusOut),this.addEventListener("wa-lazy-change",this.handleSlotChange))}async connectedCallback(){super.connectedCallback(),typeof MutationObserver<"u"&&(await this.updateComplete,this.mutationObserver=new MutationObserver(this.handleTreeChanged),this.mutationObserver.observe(this,{childList:!0,subtree:!0})),this.setAttribute("tabindex","0"),this.setAttribute("role","tree")}disconnectedCallback(){super.disconnectedCallback(),this.mutationObserver?.disconnect()}getExpandButtonIcon(e){let i=(e==="expand"?this.expandedIconSlot:this.collapsedIconSlot).assignedElements({flatten:!0})[0];if(i){let o=i.cloneNode(!0);return[o,...o.querySelectorAll("[id]")].forEach(n=>n.removeAttribute("id")),o.setAttribute("data-default",""),o.slot=`${e}-icon`,o}return null}selectItem(e){let t=[...this.selectedItems];if(this.selection==="multiple")e.selected=!e.selected,e.lazy&&(e.expanded=!0),Te(e);else if(this.selection==="leaf-multiple")e.isLeaf?e.selected=!e.selected:e.expanded=!e.expanded;else if(this.selection==="single"||e.isLeaf){let o=this.getAllTreeItems();for(let n of o)n.selected=n===e}else this.selection==="leaf"&&(e.expanded=!e.expanded);let i=this.selectedItems;(t.length!==i.length||i.some(o=>!t.includes(o)))&&Promise.all(i.map(o=>o.updateComplete)).then(()=>{this.dispatchEvent(new Xt({selection:i}))})}getAllTreeItems(){return[...this.querySelectorAll("wa-tree-item")]}focusItem(e){e?.focus()}handleKeyDown(e){if(!["ArrowDown","ArrowUp","ArrowRight","ArrowLeft","Home","End","Enter"," "].includes(e.key)||e.composedPath().some(n=>["input","textarea"].includes(n?.tagName?.toLowerCase())))return;let t=this.getFocusableItems(),i=this.matches(":dir(ltr)"),o=this.localize.dir()==="rtl";if(t.length>0){let n=t.findIndex(c=>c.matches(":focus")),s=t[n];if(!s&&(e.key==="Enter"||e.key===" "))return;e.preventDefault();let a=c=>{let m=t[Re(c,0,t.length-1)];this.focusItem(m)},l=c=>{s.expanded=c};e.key==="ArrowDown"?a(n+1):e.key==="ArrowUp"?a(n-1):i&&e.key==="ArrowRight"||o&&e.key==="ArrowLeft"?!s||s.disabled||s.expanded||s.isLeaf&&!s.lazy?a(n+1):l(!0):i&&e.key==="ArrowLeft"||o&&e.key==="ArrowRight"?!s||s.disabled||s.isLeaf||!s.expanded?a(n-1):l(!1):e.key==="Home"?a(0):e.key==="End"?a(t.length-1):(e.key==="Enter"||e.key===" ")&&s&&!s.disabled&&this.selectItem(s)}}handleClick(e){let t=e.target,i=t.closest("wa-tree-item"),o=e.composedPath().some(n=>n?.classList?.contains("expand-button"));!i||i.disabled||t!==this.clickTarget||(o?i.expanded=!i.expanded:this.selectItem(i))}handleMouseDown(e){this.clickTarget=e.target}handleSlotChange(){this.getAllTreeItems().forEach(this.initTreeItem)}async handleSelectionChange(){let e=this.selection==="multiple",t=this.selection==="leaf-multiple",i=this.getAllTreeItems();this.setAttribute("aria-multiselectable",e||t?"true":"false");for(let o of i)o.updateComplete.then(()=>{o.selectable=e||t&&o.isLeaf});e&&(await this.updateComplete,[...this.querySelectorAll(":scope > wa-tree-item")].forEach(o=>{o.updateComplete.then(()=>{Te(o,!0)})}))}get selectedItems(){let e=this.getAllTreeItems(),t=i=>i.selected;return e.filter(t)}getFocusableItems(){let e=this.getAllTreeItems(),t=new Set;return e.filter(i=>{if(i.disabled)return!1;let o=i.parentElement?.closest("[role=treeitem]");return o&&(!o.expanded||o.loading||t.has(o))&&t.add(i),!t.has(i)})}render(){return y`
      <div
        part="base tree"
        class="tree"
        @click=${this.handleClick}
        @keydown=${this.handleKeyDown}
        @mousedown=${this.handleMouseDown}
      >
        <slot @slotchange=${this.handleSlotChange}></slot>
        <span hidden aria-hidden="true"><slot name="expand-icon"></slot></span>
        <span hidden aria-hidden="true"><slot name="collapse-icon"></slot></span>
      </div>
    `}};A.css=Ie;r([L("slot:not([name])")],A.prototype,"defaultSlot",2);r([L("slot[name=expand-icon]")],A.prototype,"expandedIconSlot",2);r([L("slot[name=collapse-icon]")],A.prototype,"collapsedIconSlot",2);r([d()],A.prototype,"selection",2);r([d({attribute:"tabindex",reflect:!0,type:Number})],A.prototype,"tabIndex",2);r([d({reflect:!0})],A.prototype,"role",2);r([g("selection")],A.prototype,"handleSelectionChange",1);A=r([z("wa-tree")],A);/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Pe=w`
  :host {
    --checked-icon-color: var(--wa-color-brand-on-loud);
    --checked-icon-scale: 0.8;

    display: inline-flex;
    color: var(--wa-form-control-value-color);
    font-family: inherit;
    font-weight: var(--wa-form-control-value-font-weight);
    line-height: var(--wa-form-control-value-line-height);
    user-select: none;
    -webkit-user-select: none;
  }

  [part~='control'] {
    display: inline-flex;
    flex: 0 0 auto;
    position: relative;
    align-items: center;
    justify-content: center;
    width: var(--wa-form-control-toggle-size);
    height: var(--wa-form-control-toggle-size);
    border-color: var(--wa-form-control-border-color);
    border-radius: min(
      calc(var(--wa-form-control-toggle-size) * 0.375),
      var(--wa-border-radius-s)
    ); /* min prevents entirely circular checkbox */
    border-style: var(--wa-border-style);
    border-width: var(--wa-form-control-border-width);
    background-color: var(--wa-form-control-background-color);
    transition:
      background var(--wa-transition-normal),
      border-color var(--wa-transition-fast),
      box-shadow var(--wa-transition-fast),
      color var(--wa-transition-fast);
    transition-timing-function: var(--wa-transition-easing);

    margin-inline-end: 0.5em;
  }

  [part~='base'] {
    display: flex;
    align-items: flex-start;
    position: relative;
    color: currentColor;
    vertical-align: middle;
    cursor: pointer;
  }

  [part~='label'] {
    display: inline;
  }

  /* Checked */
  [part~='control']:has(:checked, :indeterminate) {
    color: var(--checked-icon-color);
    border-color: var(--wa-form-control-activated-color);
    background-color: var(--wa-form-control-activated-color);
  }

  /* Focus */
  [part~='control']:has(> input:focus-visible:not(:disabled)) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
  }

  /* Disabled */
  :host [part~='base']:has(input:disabled) {
    opacity: 0.5;
    cursor: not-allowed;
  }

  input {
    position: absolute;
    padding: 0;
    margin: 0;
    height: 100%;
    width: 100%;
    opacity: 0;
    pointer-events: none;
  }

  [part~='icon'] {
    display: flex;
    scale: var(--checked-icon-scale);

    /* Without this, Safari renders the icon slightly to the left */
    &::part(svg) {
      translate: 0.0009765625em;
    }

    input:not(:checked, :indeterminate) + & {
      visibility: hidden;
    }
  }

  :host([required]) [part~='label']::after {
    content: var(--wa-form-control-required-content);
    color: var(--wa-form-control-required-content-color);
    margin-inline-start: var(--wa-form-control-required-content-offset);
  }
`;/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Ue=(e={})=>{let{validationElement:t,validationProperty:i}=e;t||typeof document<"u"&&"createElement"in document&&(t=Object.assign(document.createElement("input"),{required:!0})),i||(i="value");let o={observedAttributes:["required"],message:t?.validationMessage,checkValidity(n){let s={message:"",isValid:!0,invalidKeys:[]};return(n.required??n.hasAttribute("required"))&&!n[i]&&(s.message=typeof o.message=="function"?o.message(n):o.message||"",s.isValid=!1,s.invalidKeys.push("valueMissing")),s}};return o};/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Be=w`
  :host {
    display: flex;
    flex-direction: column;
  }

  /* Treat wrapped labels, inputs, and hints as direct children of the host element */
  [part~='form-control'] {
    display: contents;
  }

  /* Label */
  :is([part~='form-control-label'], [part~='label']):has(*:not(:empty)),
  :is([part~='form-control-label'], [part~='label']).has-label {
    display: inline-flex;
    color: var(--wa-form-control-label-color);
    font-weight: var(--wa-form-control-label-font-weight);
    line-height: var(--wa-form-control-label-line-height);
    margin-block-end: 0.5em;
  }

  :host([required]) :is([part~='form-control-label'], [part~='label'])::after {
    content: var(--wa-form-control-required-content);
    margin-inline-start: var(--wa-form-control-required-content-offset);
    color: var(--wa-form-control-required-content-color);
  }

  /* Help text */
  [part~='hint'] {
    display: block;
    color: var(--wa-form-control-hint-color);
    font-weight: var(--wa-form-control-hint-font-weight);
    line-height: var(--wa-form-control-hint-line-height);
    margin-block-start: 0.5em;
    font-size: var(--wa-font-size-smaller);

    &:not(.has-slotted, .has-hint) {
      display: none;
    }
  }
`;/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Oe=class extends Event{constructor(){super("wa-invalid",{bubbles:!0,cancelable:!1,composed:!0})}};/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Ai=()=>({observedAttributes:["custom-error"],checkValidity(e){let t={message:"",isValid:!0,invalidKeys:[]};return e.customError&&(t.message=e.customError,t.isValid=!1,t.invalidKeys=["customError"]),t}}),$=class extends x{constructor(){super(),this.name=null,this.disabled=!1,this.required=!1,this.assumeInteractionOn=["input"],this.validators=[],this.valueHasChanged=!1,this.hasInteracted=!1,this.customError=null,this.emittedEvents=[],this.emitInvalid=e=>{e.target===this&&(this.hasInteracted=!0,this.dispatchEvent(new Oe))},this.handleInteraction=e=>{let t=this.emittedEvents;t.includes(e.type)||t.push(e.type),t.length===this.assumeInteractionOn?.length&&(this.hasInteracted=!0)},"addEventListener"in this&&this.addEventListener("invalid",this.emitInvalid)}static get validators(){return[Ai()]}static get observedAttributes(){let e=new Set(super.observedAttributes||[]);for(let t of this.validators)if(t.observedAttributes)for(let i of t.observedAttributes)e.add(i);return[...e]}connectedCallback(){super.connectedCallback(),this.didSSR&&!this.hasUpdated?this.updateComplete.then(()=>{this.updateValidity()}):this.updateValidity(),this.assumeInteractionOn.forEach(e=>{this.addEventListener?.(e,this.handleInteraction)})}firstUpdated(...e){super.firstUpdated(...e),this.updateValidity()}willUpdate(e){if(!!1&&e.has("customError")&&(this.customError||(this.customError=null),this.setCustomValidity(this.customError||"")),e.has("value")||e.has("disabled")||e.has("defaultValue")){let t=this.value;this.updateFormValue(t)}e.has("disabled")&&(this.customStates.set("disabled",this.disabled),(this.hasAttribute("disabled")||!!1&&!this.matches(":disabled"))&&this.toggleAttribute("disabled",this.disabled)),super.willUpdate(e),this.didSSR&&!this.hasUpdated?this.updateComplete.then(()=>this.updateValidity()):this.updateValidity()}updateFormValue(e){if(Array.isArray(e)){if(this.name){let t=new FormData;for(let i of e)t.append(this.name,i);this.setValue(t,t)}}else this.setValue(e,e)}get labels(){return this.internals.labels}getForm(){return this.internals.form}set form(e){e?this.setAttribute("form",e):this.removeAttribute("form")}get form(){return this.internals.form}get validity(){return this.internals.validity}get willValidate(){return this.internals.willValidate}get validationMessage(){return this.internals.validationMessage}checkValidity(){return this.updateValidity(),this.internals.checkValidity()}reportValidity(){return this.updateValidity(),this.hasInteracted=!0,this.internals.reportValidity()}get validationTarget(){return this.input||void 0}setValidity(...e){let t=e[0],i=e[1],o=e[2];o||(o=this.validationTarget),this.internals.setValidity(t,i,o||void 0),this.requestUpdate("validity"),this.setCustomStates()}setCustomStates(){let e=!!this.required,t=this.internals.validity.valid,i=this.hasInteracted;this.customStates.set("required",e),this.customStates.set("optional",!e),this.customStates.set("invalid",!t),this.customStates.set("valid",t),this.customStates.set("user-invalid",!t&&i),this.customStates.set("user-valid",t&&i)}setCustomValidity(e){if(!e){this.customError=null,this.setValidity({});return}this.customError=e,this.setValidity({customError:!0},e,this.validationTarget)}formResetCallback(){this.resetValidity(),this.hasInteracted=!1,this.valueHasChanged=!1,this.emittedEvents=[],this.updateValidity()}formDisabledCallback(e){this.disabled=e,this.updateValidity()}formStateRestoreCallback(e,t){this.didSSR&&!this.hasUpdated?this.updateComplete.then(()=>{this.value=e,t==="restore"&&this.resetValidity(),this.updateValidity()}):(this.value=e,t==="restore"&&this.resetValidity(),this.updateValidity())}setValue(...e){let[t,i]=e;this.internals.setFormValue(t,i)}get allValidators(){let e=this.constructor.validators||[],t=this.validators||[];return[...e,...t]}resetValidity(){this.setCustomValidity(""),this.setValidity({})}updateValidity(){if(this.disabled||this.hasAttribute("disabled")||!this.willValidate){this.resetValidity();return}let e=this.allValidators;if(!e?.length)return;let t={customError:!!this.customError},i=this.validationTarget||this.input||void 0,o="";for(let n of e){let{isValid:s,message:a,invalidKeys:l}=n.checkValidity(this);s||(o||(o=a),l?.length>=0&&l.forEach(c=>t[c]=!0))}o||(o=this.validationMessage),this.setValidity(t,o,i)}};$.formAssociated=!0;r([d({reflect:!0})],$.prototype,"name",2);r([d({type:Boolean})],$.prototype,"disabled",2);r([d({state:!0,attribute:!1})],$.prototype,"valueHasChanged",2);r([d({state:!0,attribute:!1})],$.prototype,"hasInteracted",2);r([d({attribute:"custom-error",reflect:!0})],$.prototype,"customError",2);r([d({attribute:!1,state:!0,type:Object})],$.prototype,"validity",1);/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var qe=class{constructor(e,...t){this.slotNames=[],this.handleSlotChange=i=>{let o=i.target;(this.slotNames.includes("[default]")&&!o.name||o.name&&this.slotNames.includes(o.name))&&this.host.requestUpdate()},(this.host=e).addController(this),this.slotNames=t}hasDefaultSlot(){return this.host.childNodes?[...this.host.childNodes].some(e=>{if(e.nodeType===Node.TEXT_NODE&&e.textContent.trim()!=="")return!0;if(e.nodeType===Node.ELEMENT_NODE){let t=e;if(t.tagName.toLowerCase()==="wa-visually-hidden")return!1;if(!t.hasAttribute("slot"))return!0}return!1}):!1}hasNamedSlot(e){return this.host.querySelector?.(`:scope > [slot="${e}"]`)!==null}test(e,t){return t&&this.host.didSSR&&!this.host.hasUpdated?!!this.host[t]:e==="[default]"?this.hasDefaultSlot():this.hasNamedSlot(e)}hostConnected(){let e=this.host.shadowRoot;e&&"addEventListener"in e&&e.addEventListener("slotchange",this.handleSlotChange)}hostDisconnected(){let e=this.host.shadowRoot;e&&"removeEventListener"in e&&e.removeEventListener("slotchange",this.handleSlotChange)}};/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var De={small:"s",medium:"m",large:"l"},Ve=new Set;function Ne(e,t){t in De&&!Ve.has(`${e}:${t}`)&&(Ve.add(`${e}:${t}`),console.warn(`[${e}] size="${t}" is deprecated. Use size="${De[t]}" instead. The long-form value will be removed in the next major version.`))}/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var je=w`
  :host([size='xs']) {
    font-size: var(--wa-font-size-xs);
  }

  :host([size='s']),
  :host([size='small']) {
    font-size: var(--wa-font-size-s);
  }

  :host([size='m']),
  :host([size='medium']) {
    font-size: var(--wa-font-size-m);
  }

  :host([size='l']),
  :host([size='large']) {
    font-size: var(--wa-font-size-l);
  }

  :host([size='xl']) {
    font-size: var(--wa-font-size-xl);
  }
`;/**
 * @license
 * Copyright 2018 Google LLC
 * SPDX-License-Identifier: BSD-3-Clause
 */var Lt=e=>e??p;/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var v=class extends ${constructor(){super(...arguments),this.hasSlotController=new qe(this,"hint"),this.title="",this._value=this.getAttribute("value")??null,this.size="m",this.disabled=!1,this.indeterminate=!1,this._checked=null,this.defaultChecked=this.hasAttribute("checked"),this.required=!1,this.hint=""}static get validators(){let e=[Ue({validationProperty:"checked",validationElement:Object.assign(document.createElement("input"),{type:"checkbox",required:!0})})];return[...super.validators,...e]}get value(){return this._value??"on"}set value(e){this._value=e}handleSizeChange(){Ne(this.localName,this.size)}get checked(){return this.valueHasChanged?!!this._checked:this._checked??this.defaultChecked}set checked(e){this._checked=!!e,this.valueHasChanged=!0}handleClick(){this.hasInteracted=!0,this.checked=!this.checked,this.indeterminate=!1,this.updateComplete.then(()=>{this.dispatchEvent(new Event("change",{bubbles:!0,composed:!0}))})}connectedCallback(){if(super.connectedCallback(),this.didSSR&&!this.hasUpdated){this.updateComplete.then(()=>{this.handleDefaultCheckedChange()});return}this.handleDefaultCheckedChange()}handleDefaultCheckedChange(){this.handleValueOrCheckedChange()}handleValueOrCheckedChange(){if(this.didSSR&&!this.hasUpdated){this.updateComplete.then(()=>{this.handleValueOrCheckedChange()});return}this.setValue(this.checked?this.value:null,this._value),this.updateValidity()}handleStateChange(){this.hasUpdated&&(this.input.checked=this.checked,this.input.indeterminate=this.indeterminate),this.customStates.set("checked",this.checked),this.customStates.set("indeterminate",this.indeterminate),this.updateValidity()}handleDisabledChange(){this.customStates.set("disabled",this.disabled)}willUpdate(e){super.willUpdate(e),(e.has("value")||e.has("checked")||e.has("defaultChecked")||e.has("disabled"))&&this.handleValueOrCheckedChange()}formResetCallback(){this._checked=null,super.formResetCallback(),this.handleValueOrCheckedChange()}click(){this.input.click()}focus(e){this.input.focus(e)}blur(){this.input.blur()}render(){let e=this.hasSlotController.test("hint"),t=this.hint?!0:!!e,i=!this.checked&&this.indeterminate,o=i?"indeterminate":"check",n=i?"indeterminate":"checked",s=this.didSSR&&!this.hasUpdated?this.checked:this.defaultChecked,a=this.didSSR&&!this.hasUpdated?null:rt(this.checked);return y`
      <label part="base checkbox">
        <span part="control">
          <input
            class="input"
            type="checkbox"
            title=${this.title}
            name=${Lt(this.name)}
            value=${Lt(this.value)}
            .indeterminate=${rt(this.indeterminate)}
            .checked=${Lt(a)}
            ?checked=${s}
            ?disabled=${this.disabled}
            ?required=${this.required}
            aria-checked=${this.indeterminate?"mixed":this.checked?"true":"false"}
            aria-describedby="hint"
            @click=${this.handleClick}
          />

          <wa-icon part="${n}-icon icon" library="system" name=${o}></wa-icon>
        </span>

        <slot part="label"></slot>
      </label>

      <slot
        id="hint"
        part="hint"
        name="hint"
        aria-hidden=${t?"false":"true"}
        class="${at({"has-slotted":t})}"
      >
        ${this.hint}
      </slot>
    `}};v.css=[Be,je,Pe];v.shadowRootOptions={...$.shadowRootOptions,delegatesFocus:!0};r([L('input[type="checkbox"]')],v.prototype,"input",2);r([d()],v.prototype,"title",2);r([d({reflect:!0})],v.prototype,"value",1);r([d({reflect:!0})],v.prototype,"size",2);r([g("size")],v.prototype,"handleSizeChange",1);r([d({type:Boolean})],v.prototype,"disabled",2);r([d({type:Boolean,reflect:!0})],v.prototype,"indeterminate",2);r([d({type:Boolean,attribute:!1})],v.prototype,"checked",1);r([d({type:Boolean,reflect:!0,attribute:"checked"})],v.prototype,"defaultChecked",2);r([d({type:Boolean,reflect:!0})],v.prototype,"required",2);r([d()],v.prototype,"hint",2);r([g(["checked","defaultChecked"])],v.prototype,"handleDefaultCheckedChange",1);r([g(["checked","indeterminate"])],v.prototype,"handleStateChange",1);r([g("disabled")],v.prototype,"handleDisabledChange",1);v=r([z("wa-checkbox")],v);v.disableWarning?.("change-in-update");/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var He=w`
  :host {
    --track-width: 2px;
    --track-color: var(--wa-color-neutral-fill-normal);
    --indicator-color: var(--wa-color-brand-fill-loud);
    --speed: 2s;
    --size: 1em;

    /*
      Resizing a spinner element using anything but font-size will break the animation because the animation uses em
      units. Therefore, if a spinner is used in a flex container without \`flex: none\` applied, the spinner can
      grow/shrink and break the animation. The use of \`flex: none\` on the host element prevents this by always having
      the spinner sized according to its actual dimensions.
    */
    flex: none;
    display: inline-flex;
    width: var(--size);
    height: var(--size);
  }

  svg {
    width: 100%;
    height: 100%;
    aspect-ratio: 1;
    animation: spin var(--speed) linear infinite;
  }

  .track,
  .indicator {
    --radius: calc(var(--size) / 2 - var(--track-width) / 2);
    --circumference: calc(var(--radius) * 2 * 3.141592654);

    cx: calc(var(--size) / 2);
    cy: calc(var(--size) / 2);
    r: var(--radius);
    fill: none;
    stroke-width: var(--track-width);
  }

  .track {
    stroke: var(--track-color);
  }

  .indicator {
    stroke: var(--indicator-color);
    stroke-linecap: round;
    stroke-dasharray: calc(0.597 * var(--circumference)), calc(0.796 * var(--circumference));
    stroke-dashoffset: calc(-0.04 * var(--circumference));
    animation: dash 1.5s ease-in-out infinite;
  }

  @keyframes spin {
    0% {
      transform: rotate(0deg);
    }
    100% {
      transform: rotate(360deg);
    }
  }

  @keyframes dash {
    0% {
      stroke-dasharray: calc(0.008 * var(--circumference)), calc(1.194 * var(--circumference));
      stroke-dashoffset: 0;
    }
    50% {
      stroke-dasharray: calc(0.716 * var(--circumference)), calc(1.194 * var(--circumference));
      stroke-dashoffset: calc(-0.278 * var(--circumference));
    }
    100% {
      stroke-dasharray: calc(0.716 * var(--circumference)), calc(1.194 * var(--circumference));
      stroke-dashoffset: calc(-0.987 * var(--circumference));
    }
  }
`;/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Vt=class extends x{constructor(){super(...arguments),this.localize=new W(this)}render(){return y`
      <svg
        part="base spinner"
        role="progressbar"
        aria-label=${this.localize.term("loading")}
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <circle class="track" />
        <circle class="indicator" />
      </svg>
    `}};Vt.css=He;Vt=r([z("wa-spinner")],Vt);/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var We=class extends Event{constructor(){super("wa-error",{bubbles:!0,cancelable:!1,composed:!0})}};/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Xe=class extends Event{constructor(){super("wa-load",{bubbles:!0,cancelable:!1,composed:!0})}};/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Ye=w`
  :host {
    --primary-color: currentColor;
    --primary-opacity: 1;
    --secondary-color: currentColor;
    --secondary-opacity: 0.4;
    --rotate-angle: 0deg;

    box-sizing: content-box;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    vertical-align: -0.125em;
  }

  /* #region Canvas — the box the icon is centered within (mirrors Font Awesome's icon canvas). Orthogonal to font-size. */

  /* Fixed width (default): 1.25em × 1em (20 × 16px) */
  :host(:not([canvas])),
  :host([canvas='fixed']) {
    width: 1.25em;
    height: 1em;
    min-width: 1.25em; /* <-- this is what Safari respects for intrinsic */
    min-height: 1em;
  }

  /* Auto: hug the icon's width. \`auto-width\` is the deprecated alias for canvas="auto". */
  :host([canvas='auto']),
  :host([auto-width]:not([canvas])) {
    width: auto;
    height: 1em;
  }

  /* Square: 1.25em × 1.25em (20 × 20px) */
  :host([canvas='square']) {
    width: 1.25em;
    height: 1.25em;
    min-width: 1.25em;
    min-height: 1.25em;
  }

  /* Roomy: 1.5em × 1.5em (24 × 24px) */
  :host([canvas='roomy']) {
    width: 1.5em;
    height: 1.5em;
    min-width: 1.5em;
    min-height: 1.5em;
  }

  /* #endregion */

  svg {
    /* NOTE: Avoid setting fill here. A stylesheet rule beats SVG presentation attributes, breaking stroke-based
       libraries like Lucide (fill="none" stroke="currentColor") and attribute-based mutators (issue #1733). The default
       library applies fill="currentColor" in its mutator instead. */
    height: 1em;
    overflow: visible;
    width: auto;

    /* Duotone colors with path-specific opacity fallback */
    path[data-duotone-primary] {
      color: var(--primary-color);
      opacity: var(--path-opacity, var(--primary-opacity));
    }

    path[data-duotone-secondary] {
      color: var(--secondary-color);
      opacity: var(--path-opacity, var(--secondary-opacity));
    }
  }

  /* Rotation */
  :host([rotate]) {
    transform: rotate(var(--rotate-angle, 0deg));
  }

  /* Flipping */
  :host([flip='x']) {
    transform: scaleX(-1);
  }
  :host([flip='y']) {
    transform: scaleY(-1);
  }
  :host([flip='both']) {
    transform: scale(-1, -1);
  }

  /* Rotation and Flipping combined */
  :host([rotate][flip='x']) {
    transform: rotate(var(--rotate-angle, 0deg)) scaleX(-1);
  }
  :host([rotate][flip='y']) {
    transform: rotate(var(--rotate-angle, 0deg)) scaleY(-1);
  }
  :host([rotate][flip='both']) {
    transform: rotate(var(--rotate-angle, 0deg)) scale(-1, -1);
  }

  /* #region Animations — ported from Font Awesome 7.3 (--fa-* props mapped to wa-icon's --* names) */

  :host([animation='beat']) {
    animation-name: beat;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 1s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, ease-in-out);
  }

  :host([animation='bounce']) {
    animation-name: bounce;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 1s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, cubic-bezier(0.28, 0.84, 0.42, 1));
  }

  :host([animation='fade']) {
    animation-name: fade;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 1s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, ease-in-out);
  }

  :host([animation='beat-fade']) {
    animation-name: beat-fade;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 1s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, ease-in-out);
  }

  :host([animation='flip']) {
    animation-name: flip;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 1.5s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, ease-in-out);
  }

  :host([animation='flip-360']) {
    animation-name: flip-360;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 1s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, ease-in-out);
  }

  :host([animation='shake']) {
    animation-name: shake;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 0.75s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, ease-in-out);
  }

  :host([animation='spin']) {
    animation-name: spin;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 2s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, linear);
  }

  :host([animation='spin-pulse']) {
    animation-name: spin;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 1s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, steps(8));
  }

  /* spin-reverse is FA's reverse modifier expressed as a standalone value; reverse any spin via --animation-direction: reverse */
  :host([animation='spin-reverse']) {
    animation-name: spin;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, reverse);
    animation-duration: var(--animation-duration, 2s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, linear);
  }

  :host([animation='spin-snap']) {
    animation-name: spin-snap;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 3s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, linear);
  }

  :host([animation='spin-snap-4']) {
    animation-name: spin-snap-4;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 2.4s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, linear);
  }

  :host([animation='spin-snap-8']) {
    animation-name: spin-snap-8;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 4s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, linear);
  }

  :host([animation='buzz']) {
    animation-name: buzz;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 0.6s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, linear);
  }

  :host([animation='wag']) {
    animation-name: wag;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 0.9s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, ease-out);
    transform-origin: bottom center;
  }

  :host([animation='float']) {
    animation-name: float;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 3s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, ease-in-out);
    will-change: transform;
  }

  :host([animation='swing']) {
    animation-name: swing;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 1.2s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, ease-out);
    transform-origin: top center;
  }

  :host([animation='jello']) {
    animation-name: jello;
    animation-delay: var(--animation-delay, 0s);
    animation-direction: var(--animation-direction, normal);
    animation-duration: var(--animation-duration, 0.9s);
    animation-iteration-count: var(--animation-iteration-count, infinite);
    animation-timing-function: var(--animation-timing, ease-out);
  }

  @media (prefers-reduced-motion: reduce) {
    :host([animation='beat']),
    :host([animation='bounce']),
    :host([animation='fade']),
    :host([animation='beat-fade']),
    :host([animation='flip']),
    :host([animation='flip-360']),
    :host([animation='shake']),
    :host([animation='spin']),
    :host([animation='spin-pulse']),
    :host([animation='spin-reverse']),
    :host([animation='spin-snap']),
    :host([animation='spin-snap-4']),
    :host([animation='spin-snap-8']),
    :host([animation='buzz']),
    :host([animation='wag']),
    :host([animation='float']),
    :host([animation='swing']),
    :host([animation='jello']) {
      animation: none !important;
      transition: none !important;
    }
  }

  /* #endregion */

  /* #region Keyframes — ported verbatim from Font Awesome 7.3 */

  @keyframes beat {
    0% {
      transform: scale(1);
    }
    25% {
      transform: scale(calc(1.25 * var(--beat-scale, 1.25)));
    }
    45% {
      transform: scale(calc(1.22 * var(--beat-scale, 1.22)));
    }
    65% {
      transform: scale(calc(1.25 * var(--beat-scale, 1.25)));
    }
    90% {
      transform: scale(1);
    }
  }

  @keyframes bounce {
    0% {
      transform: scale(1, 1) translateY(0);
      /* No fallback by design (ported from FA 7.3): the first segment uses the user's --animation-timing or the CSS
         initial ease, while the explicit cubic-beziers on later stops drive the bounce physics. */
      animation-timing-function: var(--animation-timing);
    }
    14% {
      transform: scale(var(--bounce-start-scale-x, 1.06), var(--bounce-start-scale-y, 0.94))
        translateY(var(--bounce-anticipation, 3px));
      animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
    }
    32% {
      transform: scale(var(--bounce-jump-scale-x, 0.94), var(--bounce-jump-scale-y, 1.12))
        translateY(calc(-1 * var(--bounce-height, 0.5em)));
      animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
    }
    52% {
      transform: scale(1, 1) translateY(calc(-1 * var(--bounce-height, 0.5em) * 1.1));
      animation-timing-function: cubic-bezier(0.5, 0, 1, 0.5);
    }
    70% {
      transform: scale(var(--bounce-land-scale-x, 1.06), var(--bounce-land-scale-y, 0.92)) translateY(0);
      animation-timing-function: cubic-bezier(0.33, 0.33, 0.66, 1);
    }
    85% {
      transform: scale(0.98, 1.04) translateY(calc(-2px * var(--bounce-rebound, 1)));
      animation-timing-function: cubic-bezier(0.33, 0, 0.66, 1);
    }
    100% {
      transform: scale(1, 1) translateY(0);
    }
  }

  @keyframes fade {
    0% {
      opacity: 1;
      transform: scale(1);
      animation-timing-function: cubic-bezier(0.2, 0, 0.4, 1);
    }
    40% {
      opacity: var(--fade-opacity, 0.4);
      transform: scale(0.98);
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    100% {
      opacity: 1;
      transform: scale(1);
    }
  }

  @keyframes beat-fade {
    0% {
      opacity: var(--beat-fade-opacity, 0.4);
      transform: scale(1);
      animation-timing-function: cubic-bezier(0.2, 0, 0.4, 1);
    }
    25% {
      opacity: calc(var(--beat-fade-opacity, 0.4) + 0.4);
      transform: scale(var(--beat-fade-scale, 1.28));
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    45% {
      opacity: 1;
      transform: scale(var(--beat-fade-scale, 1.25));
      animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    }
    65% {
      opacity: calc(var(--beat-fade-opacity, 0.4) + 0.4);
      transform: scale(var(--beat-fade-scale, 1.28));
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    100% {
      opacity: var(--beat-fade-opacity, 0.4);
      transform: scale(1);
    }
  }

  @keyframes flip {
    0% {
      transform: perspective(2em) scale(1) rotate3d(var(--flip-x, 0), var(--flip-y, 1), var(--flip-z, 0), 0deg);
      animation-timing-function: cubic-bezier(0.2, 0, 0.4, 1);
    }
    8% {
      transform: perspective(2em) scale(var(--flip-anticipation-scale, 0.95))
        rotate3d(var(--flip-x, 0), var(--flip-y, 1), var(--flip-z, 0), 0deg);
      animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
    }
    35% {
      transform: perspective(2em) scale(1)
        rotate3d(var(--flip-x, 0), var(--flip-y, 1), var(--flip-z, 0), calc(var(--flip-angle, -360deg) * 0.6));
      animation-timing-function: linear;
    }
    65% {
      transform: perspective(2em) scale(1)
        rotate3d(var(--flip-x, 0), var(--flip-y, 1), var(--flip-z, 0), calc(var(--flip-angle, -360deg) * 0.5));
      animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
    }
    92% {
      transform: perspective(2em) scale(1)
        rotate3d(
          var(--flip-x, 0),
          var(--flip-y, 1),
          var(--flip-z, 0),
          calc(var(--flip-angle, -360deg) * var(--flip-overshoot, 1.04))
        );
      animation-timing-function: cubic-bezier(0.33, 0, 0.66, 1);
    }
    100% {
      transform: perspective(2em) scale(1)
        rotate3d(var(--flip-x, 0), var(--flip-y, 1), var(--flip-z, 0), var(--flip-angle, -360deg));
    }
  }

  @keyframes flip-360 {
    0% {
      transform: perspective(2em) scale(1) rotate3d(var(--flip-x, 0), var(--flip-y, 1), var(--flip-z, 0), 0deg);
      animation-timing-function: cubic-bezier(0.2, 0, 0.4, 1);
    }
    8% {
      transform: perspective(2em) scale(var(--flip-anticipation-scale, 0.95))
        rotate3d(var(--flip-x, 0), var(--flip-y, 1), var(--flip-z, 0), 0deg);
      animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
    }
    50% {
      transform: perspective(2em) scale(1)
        rotate3d(var(--flip-x, 0), var(--flip-y, 1), var(--flip-z, 0), calc(var(--flip-angle, -360deg) * 0.6));
      animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
    }
    80% {
      transform: perspective(2em) scale(1)
        rotate3d(
          var(--flip-x, 0),
          var(--flip-y, 1),
          var(--flip-z, 0),
          calc(var(--flip-angle, -360deg) * var(--flip-overshoot, 1.04))
        );
      animation-timing-function: cubic-bezier(0.33, 0, 0.66, 1);
    }
    100% {
      transform: perspective(2em) scale(1)
        rotate3d(var(--flip-x, 0), var(--flip-y, 1), var(--flip-z, 0), var(--flip-angle, -360deg));
    }
  }

  @keyframes shake {
    0% {
      transform: rotate(0deg);
      animation-timing-function: cubic-bezier(0.2, 0, 0.8, 1);
    }
    8% {
      transform: rotate(35deg) translateX(1px);
      animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
    }
    20% {
      transform: rotate(-22deg) translateX(-1px);
      animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
    }
    35% {
      transform: rotate(15deg) translateX(1px);
      animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
    }
    50% {
      transform: rotate(-9deg);
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    65% {
      transform: rotate(5deg);
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    78% {
      transform: rotate(-3deg);
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    90% {
      transform: rotate(1deg);
      animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    }
    100% {
      transform: rotate(0deg);
    }
  }

  @keyframes spin {
    0% {
      transform: rotate(0deg);
    }
    100% {
      transform: rotate(360deg);
    }
  }

  @keyframes spin-snap {
    0% {
      transform: rotate(0deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    12% {
      transform: rotate(60deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    16.67% {
      transform: rotate(60deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    28.67% {
      transform: rotate(120deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    33.33% {
      transform: rotate(120deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    45.33% {
      transform: rotate(180deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    50% {
      transform: rotate(180deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    62% {
      transform: rotate(240deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    66.67% {
      transform: rotate(240deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    78.67% {
      transform: rotate(300deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    83.33% {
      transform: rotate(300deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    95.33% {
      transform: rotate(360deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    100% {
      transform: rotate(360deg);
    }
  }

  @keyframes spin-snap-4 {
    0% {
      transform: rotate(0deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    15% {
      transform: rotate(90deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    25% {
      transform: rotate(90deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    40% {
      transform: rotate(180deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    50% {
      transform: rotate(180deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    65% {
      transform: rotate(270deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    75% {
      transform: rotate(270deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    90% {
      transform: rotate(360deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    100% {
      transform: rotate(360deg);
    }
  }

  @keyframes spin-snap-8 {
    0% {
      transform: rotate(0deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    9% {
      transform: rotate(45deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    12.5% {
      transform: rotate(45deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    21.5% {
      transform: rotate(90deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    25% {
      transform: rotate(90deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    34% {
      transform: rotate(135deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    37.5% {
      transform: rotate(135deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    46.5% {
      transform: rotate(180deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    50% {
      transform: rotate(180deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    59% {
      transform: rotate(225deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    62.5% {
      transform: rotate(225deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    71.5% {
      transform: rotate(270deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    75% {
      transform: rotate(270deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    84% {
      transform: rotate(315deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    87.5% {
      transform: rotate(315deg);
      animation-timing-function: cubic-bezier(0, 0, 0.2, 1);
    }
    96.5% {
      transform: rotate(360deg);
      animation-timing-function: cubic-bezier(0.8, 0, 1, 1);
    }
    100% {
      transform: rotate(360deg);
    }
  }

  @keyframes buzz {
    0% {
      transform: translateX(0) rotate(0deg);
      animation-timing-function: cubic-bezier(0.1, 0, 0.9, 1);
    }
    5% {
      transform: translateX(var(--buzz-distance, 4px)) rotate(0.5deg);
    }
    10% {
      transform: translateX(calc(-1 * var(--buzz-distance, 4px))) rotate(-0.5deg);
    }
    15% {
      transform: translateX(var(--buzz-distance, 4px)) rotate(0.3deg);
    }
    20% {
      transform: translateX(calc(-1 * var(--buzz-distance, 4px))) rotate(-0.3deg);
    }
    25% {
      transform: translateX(calc(var(--buzz-distance, 4px) * 0.7)) rotate(0.2deg);
    }
    30% {
      transform: translateX(calc(-1 * var(--buzz-distance, 4px) * 0.7)) rotate(-0.2deg);
    }
    35% {
      transform: translateX(calc(var(--buzz-distance, 4px) * 0.4)) rotate(0.1deg);
    }
    40% {
      transform: translateX(0) rotate(0deg);
    }
    100% {
      transform: translateX(0) rotate(0deg);
    }
  }

  @keyframes wag {
    0% {
      transform: rotate(0deg);
      animation-timing-function: cubic-bezier(0.2, 0, 0.6, 1);
    }
    12% {
      transform: rotate(var(--wag-angle, 12deg));
      animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    }
    24% {
      transform: rotate(2deg);
      animation-timing-function: cubic-bezier(0.2, 0, 0.6, 1);
    }
    36% {
      transform: rotate(calc(var(--wag-angle, 12deg) * 0.85));
      animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    }
    48% {
      transform: rotate(1deg);
      animation-timing-function: cubic-bezier(0.2, 0, 0.6, 1);
    }
    58% {
      transform: rotate(calc(var(--wag-angle, 12deg) * 0.6));
      animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    }
    68% {
      transform: rotate(0deg);
    }
    100% {
      transform: rotate(0deg);
    }
  }

  @keyframes float {
    0% {
      transform: translateY(0) translateX(0) rotate(0deg)
        scale(var(--float-squash-x, 1.02), var(--float-squash-y, 0.98));
      animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
    }
    15% {
      transform: translateY(calc(-0.4 * var(--float-height, 6px))) translateX(var(--float-drift, 1px))
        rotate(var(--float-tilt, 1deg)) scale(1, 1);
      animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
    }
    35% {
      transform: translateY(calc(-1 * var(--float-height, 6px))) translateX(0) rotate(0deg)
        scale(var(--float-stretch-x, 0.98), var(--float-stretch-y, 1.03));
      animation-timing-function: cubic-bezier(0.5, 0, 0.5, 0);
    }
    50% {
      transform: translateY(calc(-0.92 * var(--float-height, 6px))) translateX(calc(-0.5 * var(--float-drift, 1px)))
        rotate(calc(-0.5 * var(--float-tilt, 1deg))) scale(0.995, 1.01);
      animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
    }
    70% {
      transform: translateY(calc(-0.3 * var(--float-height, 6px))) translateX(calc(-1 * var(--float-drift, 1px)))
        rotate(calc(-1 * var(--float-tilt, 1deg))) scale(1, 1);
      animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
    }
    90% {
      transform: translateY(calc(0.05 * var(--float-height, 6px))) translateX(0) rotate(0deg)
        scale(var(--float-squash-x, 1.02), var(--float-squash-y, 0.98));
      animation-timing-function: cubic-bezier(0.33, 0, 0.66, 1);
    }
    100% {
      transform: translateY(0) translateX(0) rotate(0deg)
        scale(var(--float-squash-x, 1.02), var(--float-squash-y, 0.98));
    }
  }

  @keyframes swing {
    0% {
      transform: rotate(0deg);
      animation-timing-function: cubic-bezier(0.2, 0, 0.8, 1);
    }
    8% {
      transform: rotate(var(--swing-angle, 22deg));
      animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
    }
    18% {
      transform: rotate(calc(-1 * var(--swing-angle, 22deg) * 0.85));
      animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
    }
    28% {
      transform: rotate(calc(var(--swing-angle, 22deg) * 0.65));
      animation-timing-function: cubic-bezier(0.35, 0, 0.65, 1);
    }
    38% {
      transform: rotate(calc(-1 * var(--swing-angle, 22deg) * 0.45));
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    48% {
      transform: rotate(calc(var(--swing-angle, 22deg) * 0.25));
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    56% {
      transform: rotate(calc(-1 * var(--swing-angle, 22deg) * 0.1));
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    64% {
      transform: rotate(0deg);
    }
    100% {
      transform: rotate(0deg);
    }
  }

  @keyframes jello {
    0% {
      transform: scale(1, 1);
      animation-timing-function: cubic-bezier(0.2, 0, 0.8, 1);
    }
    12% {
      transform: scale(var(--jello-scale-x, 1.15), calc(2 - var(--jello-scale-x, 1.15)));
      animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
    }
    24% {
      transform: scale(calc(2 - var(--jello-scale-y, 1.12)), var(--jello-scale-y, 1.12));
      animation-timing-function: cubic-bezier(0.3, 0, 0.7, 1);
    }
    36% {
      transform: scale(
        calc(1 + (var(--jello-scale-x, 1.15) - 1) * 0.5),
        calc(2 - (1 + (var(--jello-scale-x, 1.15) - 1) * 0.5))
      );
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    48% {
      transform: scale(
        calc(2 - (1 + (var(--jello-scale-y, 1.12) - 1) * 0.3)),
        calc(1 + (var(--jello-scale-y, 1.12) - 1) * 0.3)
      );
      animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1);
    }
    58% {
      transform: scale(1.02, 0.98);
      animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
    }
    68% {
      transform: scale(1, 1);
    }
    100% {
      transform: scale(1, 1);
    }
  }

  /* #endregion */
`;/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var zi="",Nt="";function Ke(){return zi.replace(/\/$/,"")}function $i(e){Nt=e}function Ge(){if(!Nt){let e=document.querySelector("[data-fa-kit-code]");e&&$i(e.getAttribute("data-fa-kit-code")||"")}return Nt}/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Ze="7.3.0";function _i(e,t,i){let o="solid";return t==="chisel"&&(o="chisel-regular"),t==="etch"&&(o="etch-solid"),t==="graphite"&&(o="graphite-thin"),t==="jelly"&&(o="jelly-regular",i==="duo-regular"&&(o="jelly-duo-regular"),i==="fill-regular"&&(o="jelly-fill-regular")),t==="jelly-duo"&&(o="jelly-duo-regular"),t==="jelly-fill"&&(o="jelly-fill-regular"),t==="notdog"&&(i==="solid"&&(o="notdog-solid"),i==="duo-solid"&&(o="notdog-duo-solid")),t==="notdog-duo"&&(o="notdog-duo-solid"),t==="slab"&&((i==="solid"||i==="regular")&&(o="slab-regular"),i==="press-regular"&&(o="slab-press-regular")),t==="slab-press"&&(o="slab-press-regular"),t==="slab-duo"&&(o="slab-duo-regular"),t==="slab-press-duo"&&(o="slab-press-duo-regular"),t==="thumbprint"&&(o="thumbprint-light"),t==="utility"&&(o="utility-semibold"),t==="utility-duo"&&(o="utility-duo-semibold"),t==="utility-fill"&&(o="utility-fill-semibold"),t==="whiteboard"&&(o="whiteboard-semibold"),t==="mosaic"&&(o="mosaic-solid"),t==="pixel"&&(o="pixel-regular"),t==="vellum"&&(o="vellum-solid"),t==="classic"&&(i==="thin"&&(o="thin"),i==="light"&&(o="light"),i==="regular"&&(o="regular"),i==="solid"&&(o="solid")),t==="duotone"&&(i==="thin"&&(o="duotone-thin"),i==="light"&&(o="duotone-light"),i==="regular"&&(o="duotone-regular"),i==="solid"&&(o="duotone")),t==="sharp"&&(i==="thin"&&(o="sharp-thin"),i==="light"&&(o="sharp-light"),i==="regular"&&(o="sharp-regular"),i==="solid"&&(o="sharp-solid")),t==="sharp-duotone"&&(i==="thin"&&(o="sharp-duotone-thin"),i==="light"&&(o="sharp-duotone-light"),i==="regular"&&(o="sharp-duotone-regular"),i==="solid"&&(o="sharp-duotone-solid")),t==="brands"&&(o="brands"),o}function Ei(e,t,i){let o=_i(e,t,i),n=Ke();if(n)return`${n}/${o}/${e}.svg`;let s=Ge();return s.length>0?`https://ka-p.fontawesome.com/releases/v${Ze}/svgs/${o}/${e}.svg?token=${encodeURIComponent(s)}`:`https://ka-f.fontawesome.com/releases/v${Ze}/svgs/${o}/${e}.svg`}var Si={name:"default",resolver:(e,t="classic",i="solid")=>Ei(e,t,i),mutator:(e,t)=>{if(e.hasAttribute("fill")||e.setAttribute("fill","currentColor"),t?.family&&!e.hasAttribute("data-duotone-initialized")){let{family:i,variant:o}=t;if(i==="duotone"||i==="sharp-duotone"||i==="notdog-duo"||i==="notdog"&&o==="duo-solid"||i==="jelly-duo"||i==="jelly"&&o==="duo-regular"||i==="utility-duo"||i==="slab-duo"||i==="slab-press-duo"||i==="thumbprint"){let n=[...e.querySelectorAll("path")],s=n.find(l=>!l.hasAttribute("opacity")),a=n.find(l=>l.hasAttribute("opacity"));if(!s||!a)return;if(s.setAttribute("data-duotone-primary",""),a.setAttribute("data-duotone-secondary",""),t.swapOpacity&&s&&a){let l=a.getAttribute("opacity")||"0.4";s.style.setProperty("--path-opacity",l),a.style.setProperty("--path-opacity","1")}e.setAttribute("data-duotone-initialized","")}}}},Je=Si;/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */function ki(e){return`data:image/svg+xml,${encodeURIComponent(e)}`}var jt={solid:{backward:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M236.3 107.1C247.9 96 265 92.9 279.7 99.2C294.4 105.5 304 120 304 136L304 272.3L476.3 107.2C487.9 96 505 92.9 519.7 99.2C534.4 105.5 544 120 544 136L544 504C544 520 534.4 534.5 519.7 540.8C505 547.1 487.9 544 476.3 532.9L304 367.7L304 504C304 520 294.4 534.5 279.7 540.8C265 547.1 247.9 544 236.3 532.9L44.3 348.9C36.5 341.3 32 330.9 32 320C32 309.1 36.5 298.7 44.3 291.1L236.3 107.1z"/></svg>',"backward-step":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M491 100.8C478.1 93.8 462.3 94.5 450 102.6L192 272.1L192 128C192 110.3 177.7 96 160 96C142.3 96 128 110.3 128 128L128 512C128 529.7 142.3 544 160 544C177.7 544 192 529.7 192 512L192 367.9L450 537.5C462.3 545.6 478 546.3 491 539.3C504 532.3 512 518.8 512 504.1L512 136.1C512 121.4 503.9 107.9 491 100.9z"/></svg>',"angles-left":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M77.3 256 214.7 118.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0l-160 160c-12.5 12.5-12.5 32.8 0 45.3l160 160c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L77.3 256zm192 0L406.7 118.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0l-160 160c-12.5 12.5-12.5 32.8 0 45.3l160 160c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L269.3 256z"/></svg>',"angles-right":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M434.7 256 297.3 118.6c-12.5-12.5-12.5-32.8 0-45.3s32.8-12.5 45.3 0l160 160c12.5 12.5 12.5 32.8 0 45.3l-160 160c-12.5 12.5-32.8 12.5-45.3 0s-12.5-32.8 0-45.3L434.7 256zm-192 0L105.3 118.6c-12.5-12.5-12.5-32.8 0-45.3s32.8-12.5 45.3 0l160 160c12.5 12.5 12.5 32.8 0 45.3l-160 160c-12.5 12.5-32.8 12.5-45.3 0s-12.5-32.8 0-45.3L242.7 256z"/></svg>',check:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M434.8 70.1c14.3 10.4 17.5 30.4 7.1 44.7l-256 352c-5.5 7.6-14 12.3-23.4 13.1s-18.5-2.7-25.1-9.3l-128-128c-12.5-12.5-12.5-32.8 0-45.3s32.8-12.5 45.3 0l101.5 101.5 234-321.7c10.4-14.3 30.4-17.5 44.7-7.1z"/></svg>',"chevron-down":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M201.4 406.6c12.5 12.5 32.8 12.5 45.3 0l192-192c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L224 338.7 54.6 169.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3l192 192z"/></svg>',"chevron-left":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M9.4 233.4c-12.5 12.5-12.5 32.8 0 45.3l192 192c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L77.3 256 246.6 86.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0l-192 192z"/></svg>',"chevron-right":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M311.1 233.4c12.5 12.5 12.5 32.8 0 45.3l-192 192c-12.5 12.5-32.8 12.5-45.3 0s-12.5-32.8 0-45.3L243.2 256 73.9 86.6c-12.5-12.5-12.5-32.8 0-45.3s32.8-12.5 45.3 0l192 192z"/></svg>',circle:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M0 256a256 256 0 1 1 512 0 256 256 0 1 1 -512 0z"/></svg>',"closed-captioning":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M64 192C64 156.7 92.7 128 128 128L512 128C547.3 128 576 156.7 576 192L576 448C576 483.3 547.3 512 512 512L128 512C92.7 512 64 483.3 64 448L64 192zM216 272L248 272C252.4 272 256 275.6 256 280C256 293.3 266.7 304 280 304C293.3 304 304 293.3 304 280C304 249.1 278.9 224 248 224L216 224C185.1 224 160 249.1 160 280L160 360C160 390.9 185.1 416 216 416L248 416C278.9 416 304 390.9 304 360C304 346.7 293.3 336 280 336C266.7 336 256 346.7 256 360C256 364.4 252.4 368 248 368L216 368C211.6 368 208 364.4 208 360L208 280C208 275.6 211.6 272 216 272zM384 280C384 275.6 387.6 272 392 272L424 272C428.4 272 432 275.6 432 280C432 293.3 442.7 304 456 304C469.3 304 480 293.3 480 280C480 249.1 454.9 224 424 224L392 224C361.1 224 336 249.1 336 280L336 360C336 390.9 361.1 416 392 416L424 416C454.9 416 480 390.9 480 360C480 346.7 469.3 336 456 336C442.7 336 432 346.7 432 360C432 364.4 428.4 368 424 368L392 368C387.6 368 384 364.4 384 360L384 280z"/></svg>',"closed-captioning-slash":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M39 39.1C48.4 29.7 63.6 29.7 72.9 39.1L161.8 128L512 128C547.3 128 576 156.7 576 192L576 448C576 473.5 561.1 495.4 539.6 505.8L601 567.1C610.4 576.5 610.4 591.7 601 601C591.6 610.3 576.4 610.4 567.1 601L39 73.1C29.7 63.7 29.7 48.5 39 39.1zM384 350.1L384 279.9C384 275.5 387.6 271.9 392 271.9L424 271.9C428.4 271.9 432 275.5 432 279.9C432 293.2 442.7 303.9 456 303.9C469.3 303.9 480 293.2 480 279.9C480 249 454.9 223.9 424 223.9L392 223.9C361.1 223.9 336 249 336 279.9L336 302.1L384 350.1zM445.5 411.6C465.7 403.2 480 383.2 480 359.9C480 346.6 469.3 335.9 456 335.9C442.7 335.9 432 346.6 432 359.9C432 364.3 428.4 367.9 424 367.9L401.8 367.9L445.5 411.6zM162.3 264.1C160.8 269.1 160 274.5 160 280L160 360C160 390.9 185.1 416 216 416L248 416C266.1 416 282.1 407.5 292.4 394.2L410.2 512L128 512C92.7 512 64 483.3 64 448L64 192C64 184.2 65.4 176.7 68 169.8L162.3 264.1zM256.1 357.9C256 358.6 256 359.3 256 360C256 364.4 252.4 368 248 368L216 368C211.6 368 208 364.4 208 360L208 309.8L256.1 357.9z"/></svg>',compress:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M160 64c0-17.7-14.3-32-32-32S96 46.3 96 64l0 64-64 0c-17.7 0-32 14.3-32 32s14.3 32 32 32l96 0c17.7 0 32-14.3 32-32l0-96zM32 320c-17.7 0-32 14.3-32 32s14.3 32 32 32l64 0 0 64c0 17.7 14.3 32 32 32s32-14.3 32-32l0-96c0-17.7-14.3-32-32-32l-96 0zM352 64c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 96c0 17.7 14.3 32 32 32l96 0c17.7 0 32-14.3 32-32s-14.3-32-32-32l-64 0 0-64zM320 320c-17.7 0-32 14.3-32 32l0 96c0 17.7 14.3 32 32 32s32-14.3 32-32l0-64 64 0c17.7 0 32-14.3 32-32s-14.3-32-32-32l-96 0z"/></svg>',ellipsis:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.3.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path d="M96 320C96 289.1 121.1 264 152 264C182.9 264 208 289.1 208 320C208 350.9 182.9 376 152 376C121.1 376 96 350.9 96 320zM264 320C264 289.1 289.1 264 320 264C350.9 264 376 289.1 376 320C376 350.9 350.9 376 320 376C289.1 376 264 350.9 264 320zM488 264C518.9 264 544 289.1 544 320C544 350.9 518.9 376 488 376C457.1 376 432 350.9 432 320C432 289.1 457.1 264 488 264z"/></svg>',"ellipsis-vertical":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M320 208C289.1 208 264 182.9 264 152C264 121.1 289.1 96 320 96C350.9 96 376 121.1 376 152C376 182.9 350.9 208 320 208zM320 432C350.9 432 376 457.1 376 488C376 518.9 350.9 544 320 544C289.1 544 264 518.9 264 488C264 457.1 289.1 432 320 432zM376 320C376 350.9 350.9 376 320 376C289.1 376 264 350.9 264 320C264 289.1 289.1 264 320 264C350.9 264 376 289.1 376 320z"/></svg>',expand:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M128 96C110.3 96 96 110.3 96 128L96 224C96 241.7 110.3 256 128 256C145.7 256 160 241.7 160 224L160 160L224 160C241.7 160 256 145.7 256 128C256 110.3 241.7 96 224 96L128 96zM160 416C160 398.3 145.7 384 128 384C110.3 384 96 398.3 96 416L96 512C96 529.7 110.3 544 128 544L224 544C241.7 544 256 529.7 256 512C256 494.3 241.7 480 224 480L160 480L160 416zM416 96C398.3 96 384 110.3 384 128C384 145.7 398.3 160 416 160L480 160L480 224C480 241.7 494.3 256 512 256C529.7 256 544 241.7 544 224L544 128C544 110.3 529.7 96 512 96L416 96zM544 416C544 398.3 529.7 384 512 384C494.3 384 480 398.3 480 416L480 480L416 480C398.3 480 384 494.3 384 512C384 529.7 398.3 544 416 544L512 544C529.7 544 544 529.7 544 512L544 416z"/></svg>',eyedropper:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M341.6 29.2l-101.6 101.6-9.4-9.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3l160 160c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3l-9.4-9.4 101.6-101.6c39-39 39-102.2 0-141.1s-102.2-39-141.1 0zM55.4 323.3c-15 15-23.4 35.4-23.4 56.6l0 42.4-26.6 39.9c-8.5 12.7-6.8 29.6 4 40.4s27.7 12.5 40.4 4l39.9-26.6 42.4 0c21.2 0 41.6-8.4 56.6-23.4l109.4-109.4-45.3-45.3-109.4 109.4c-3 3-7.1 4.7-11.3 4.7l-36.1 0 0-36.1c0-4.2 1.7-8.3 4.7-11.3l109.4-109.4-45.3-45.3-109.4 109.4z"/></svg>',forward:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M403.7 107.1C392.1 96 375 92.9 360.3 99.2C345.6 105.5 336 120 336 136L336 272.3L163.7 107.2C152.1 96 135 92.9 120.3 99.2C105.6 105.5 96 120 96 136L96 504C96 520 105.6 534.5 120.3 540.8C135 547.1 152.1 544 163.7 532.9L336 367.7L336 504C336 520 345.6 534.5 360.3 540.8C375 547.1 392.1 544 403.7 532.9L595.7 348.9C603.6 341.4 608 330.9 608 320C608 309.1 603.5 298.7 595.7 291.1L403.7 107.1z"/></svg>',file:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free 7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M192 64C156.7 64 128 92.7 128 128L128 512C128 547.3 156.7 576 192 576L448 576C483.3 576 512 547.3 512 512L512 234.5C512 217.5 505.3 201.2 493.3 189.2L386.7 82.7C374.7 70.7 358.5 64 341.5 64L192 64zM453.5 240L360 240C346.7 240 336 229.3 336 216L336 122.5L453.5 240z"/></svg>',"file-audio":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free 7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M128 128C128 92.7 156.7 64 192 64L341.5 64C358.5 64 374.8 70.7 386.8 82.7L493.3 189.3C505.3 201.3 512 217.6 512 234.6L512 512C512 547.3 483.3 576 448 576L192 576C156.7 576 128 547.3 128 512L128 128zM336 122.5L336 216C336 229.3 346.7 240 360 240L453.5 240L336 122.5zM389.8 307.7C380.7 301.4 368.3 303.6 362 312.7C355.7 321.8 357.9 334.2 367 340.5C390.9 357.2 406.4 384.8 406.4 416C406.4 447.2 390.8 474.9 367 491.5C357.9 497.8 355.7 510.3 362 519.3C368.3 528.3 380.8 530.6 389.8 524.3C423.9 500.5 446.4 460.8 446.4 416C446.4 371.2 424 331.5 389.8 307.7zM208 376C199.2 376 192 383.2 192 392L192 440C192 448.8 199.2 456 208 456L232 456L259.2 490C262.2 493.8 266.8 496 271.7 496L272 496C280.8 496 288 488.8 288 480L288 352C288 343.2 280.8 336 272 336L271.7 336C266.8 336 262.2 338.2 259.2 342L232 376L208 376zM336 448.2C336 458.9 346.5 466.4 354.9 459.8C367.8 449.5 376 433.7 376 416C376 398.3 367.8 382.5 354.9 372.2C346.5 365.5 336 373.1 336 383.8L336 448.3z"/></svg>',"file-code":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free 7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M128 128C128 92.7 156.7 64 192 64L341.5 64C358.5 64 374.8 70.7 386.8 82.7L493.3 189.3C505.3 201.3 512 217.6 512 234.6L512 512C512 547.3 483.3 576 448 576L192 576C156.7 576 128 547.3 128 512L128 128zM336 122.5L336 216C336 229.3 346.7 240 360 240L453.5 240L336 122.5zM282.2 359.6C290.8 349.5 289.7 334.4 279.6 325.8C269.5 317.2 254.4 318.3 245.8 328.4L197.8 384.4C190.1 393.4 190.1 406.6 197.8 415.6L245.8 471.6C254.4 481.7 269.6 482.8 279.6 474.2C289.6 465.6 290.8 450.4 282.2 440.4L247.6 400L282.2 359.6zM394.2 328.4C385.6 318.3 370.4 317.2 360.4 325.8C350.4 334.4 349.2 349.6 357.8 359.6L392.4 400L357.8 440.4C349.2 450.5 350.3 465.6 360.4 474.2C370.5 482.8 385.6 481.7 394.2 471.6L442.2 415.6C449.9 406.6 449.9 393.4 442.2 384.4L394.2 328.4z"/></svg>',"file-excel":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free 7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M128 128C128 92.7 156.7 64 192 64L341.5 64C358.5 64 374.8 70.7 386.8 82.7L493.3 189.3C505.3 201.3 512 217.6 512 234.6L512 512C512 547.3 483.3 576 448 576L192 576C156.7 576 128 547.3 128 512L128 128zM336 122.5L336 216C336 229.3 346.7 240 360 240L453.5 240L336 122.5zM292 330.7C284.6 319.7 269.7 316.7 258.7 324C247.7 331.3 244.7 346.3 252 357.3L291.2 416L252 474.7C244.6 485.7 247.6 500.6 258.7 508C269.8 515.4 284.6 512.4 292 501.3L320 459.3L348 501.3C355.4 512.3 370.3 515.3 381.3 508C392.3 500.7 395.3 485.7 388 474.7L348.8 416L388 357.3C395.4 346.3 392.4 331.4 381.3 324C370.2 316.6 355.4 319.6 348 330.7L320 372.7L292 330.7z"/></svg>',"file-image":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free 7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M128 128C128 92.7 156.7 64 192 64L341.5 64C358.5 64 374.8 70.7 386.8 82.7L493.3 189.3C505.3 201.3 512 217.6 512 234.6L512 512C512 547.3 483.3 576 448 576L192 576C156.7 576 128 547.3 128 512L128 128zM336 122.5L336 216C336 229.3 346.7 240 360 240L453.5 240L336 122.5zM256 320C256 302.3 241.7 288 224 288C206.3 288 192 302.3 192 320C192 337.7 206.3 352 224 352C241.7 352 256 337.7 256 320zM220.6 512L419.4 512C435.2 512 448 499.2 448 483.4C448 476.1 445.2 469 440.1 463.7L343.3 361.9C337.3 355.6 328.9 352 320.1 352L319.8 352C311 352 302.7 355.6 296.6 361.9L199.9 463.7C194.8 469 192 476.1 192 483.4C192 499.2 204.8 512 220.6 512z"/></svg>',"file-pdf":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free 7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M128 64C92.7 64 64 92.7 64 128L64 512C64 547.3 92.7 576 128 576L208 576L208 464C208 428.7 236.7 400 272 400L448 400L448 234.5C448 217.5 441.3 201.2 429.3 189.2L322.7 82.7C310.7 70.7 294.5 64 277.5 64L128 64zM389.5 240L296 240C282.7 240 272 229.3 272 216L272 122.5L389.5 240zM272 444C261 444 252 453 252 464L252 592C252 603 261 612 272 612C283 612 292 603 292 592L292 564L304 564C337.1 564 364 537.1 364 504C364 470.9 337.1 444 304 444L272 444zM304 524L292 524L292 484L304 484C315 484 324 493 324 504C324 515 315 524 304 524zM400 444C389 444 380 453 380 464L380 592C380 603 389 612 400 612L432 612C460.7 612 484 588.7 484 560L484 496C484 467.3 460.7 444 432 444L400 444zM420 572L420 484L432 484C438.6 484 444 489.4 444 496L444 560C444 566.6 438.6 572 432 572L420 572zM508 464L508 592C508 603 517 612 528 612C539 612 548 603 548 592L548 548L576 548C587 548 596 539 596 528C596 517 587 508 576 508L548 508L548 484L576 484C587 484 596 475 596 464C596 453 587 444 576 444L528 444C517 444 508 453 508 464z"/></svg>',"file-powerpoint":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free 7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M128 128C128 92.7 156.7 64 192 64L341.5 64C358.5 64 374.8 70.7 386.8 82.7L493.3 189.3C505.3 201.3 512 217.6 512 234.6L512 512C512 547.3 483.3 576 448 576L192 576C156.7 576 128 547.3 128 512L128 128zM336 122.5L336 216C336 229.3 346.7 240 360 240L453.5 240L336 122.5zM280 320C266.7 320 256 330.7 256 344L256 488C256 501.3 266.7 512 280 512C293.3 512 304 501.3 304 488L304 464L328 464C367.8 464 400 431.8 400 392C400 352.2 367.8 320 328 320L280 320zM328 416L304 416L304 368L328 368C341.3 368 352 378.7 352 392C352 405.3 341.3 416 328 416z"/></svg>',"file-video":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free 7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M128 128C128 92.7 156.7 64 192 64L341.5 64C358.5 64 374.8 70.7 386.8 82.7L493.3 189.3C505.3 201.3 512 217.6 512 234.6L512 512C512 547.3 483.3 576 448 576L192 576C156.7 576 128 547.3 128 512L128 128zM336 122.5L336 216C336 229.3 346.7 240 360 240L453.5 240L336 122.5zM208 368L208 464C208 481.7 222.3 496 240 496L336 496C353.7 496 368 481.7 368 464L368 440L403 475C406.2 478.2 410.5 480 415 480C424.4 480 432 472.4 432 463L432 368.9C432 359.5 424.4 351.9 415 351.9C410.5 351.9 406.2 353.7 403 356.9L368 391.9L368 367.9C368 350.2 353.7 335.9 336 335.9L240 335.9C222.3 335.9 208 350.2 208 367.9z"/></svg>',"file-word":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free 7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M128 128C128 92.7 156.7 64 192 64L341.5 64C358.5 64 374.8 70.7 386.8 82.7L493.3 189.3C505.3 201.3 512 217.6 512 234.6L512 512C512 547.3 483.3 576 448 576L192 576C156.7 576 128 547.3 128 512L128 128zM336 122.5L336 216C336 229.3 346.7 240 360 240L453.5 240L336 122.5zM263.4 338.8C260.5 325.9 247.7 317.7 234.8 320.6C221.9 323.5 213.7 336.3 216.6 349.2L248.6 493.2C250.9 503.7 260 511.4 270.8 512C281.6 512.6 291.4 505.9 294.8 495.6L320 419.9L345.2 495.6C348.6 505.8 358.4 512.5 369.2 512C380 511.5 389.1 503.8 391.4 493.2L423.4 349.2C426.3 336.3 418.1 323.4 405.2 320.6C392.3 317.8 379.4 325.9 376.6 338.8L363.4 398.2L342.8 336.4C339.5 326.6 330.4 320 320 320C309.6 320 300.5 326.6 297.2 336.4L276.6 398.2L263.4 338.8z"/></svg>',"file-zipper":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free 7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M128 128C128 92.7 156.7 64 192 64L341.5 64C358.5 64 374.8 70.7 386.8 82.7L493.3 189.3C505.3 201.3 512 217.6 512 234.6L512 512C512 547.3 483.3 576 448 576L192 576C156.7 576 128 547.3 128 512L128 128zM336 122.5L336 216C336 229.3 346.7 240 360 240L453.5 240L336 122.5zM192 136C192 149.3 202.7 160 216 160L264 160C277.3 160 288 149.3 288 136C288 122.7 277.3 112 264 112L216 112C202.7 112 192 122.7 192 136zM192 232C192 245.3 202.7 256 216 256L264 256C277.3 256 288 245.3 288 232C288 218.7 277.3 208 264 208L216 208C202.7 208 192 218.7 192 232zM256 304L224 304C206.3 304 192 318.3 192 336L192 384C192 410.5 213.5 432 240 432C266.5 432 288 410.5 288 384L288 336C288 318.3 273.7 304 256 304zM240 368C248.8 368 256 375.2 256 384C256 392.8 248.8 400 240 400C231.2 400 224 392.8 224 384C224 375.2 231.2 368 240 368z"/></svg>',"forward-step":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 384 512"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M21 36.8c12.9-7 28.7-6.3 41 1.8L320 208.1 320 64c0-17.7 14.3-32 32-32s32 14.3 32 32l0 384c0 17.7-14.3 32-32 32s-32-14.3-32-32l0-144.1-258 169.6c-12.3 8.1-28 8.8-41 1.8S0 454.7 0 440L0 72C0 57.3 8.1 43.8 21 36.8z"/></svg>',gauge:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M0 256a256 256 0 1 1 512 0 256 256 0 1 1 -512 0zm320 96c0-26.9-16.5-49.9-40-59.3L280 120c0-13.3-10.7-24-24-24s-24 10.7-24 24l0 172.7c-23.5 9.5-40 32.5-40 59.3 0 35.3 28.7 64 64 64s64-28.7 64-64zM144 176a32 32 0 1 0 0-64 32 32 0 1 0 0 64zm-16 80a32 32 0 1 0 -64 0 32 32 0 1 0 64 0zm288 32a32 32 0 1 0 0-64 32 32 0 1 0 0 64zM400 144a32 32 0 1 0 -64 0 32 32 0 1 0 64 0z"/></svg>',gear:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M259.1 73.5C262.1 58.7 275.2 48 290.4 48L350.2 48C365.4 48 378.5 58.7 381.5 73.5L396 143.5C410.1 149.5 423.3 157.2 435.3 166.3L503.1 143.8C517.5 139 533.3 145 540.9 158.2L570.8 210C578.4 223.2 575.7 239.8 564.3 249.9L511 297.3C511.9 304.7 512.3 312.3 512.3 320C512.3 327.7 511.8 335.3 511 342.7L564.4 390.2C575.8 400.3 578.4 417 570.9 430.1L541 481.9C533.4 495 517.6 501.1 503.2 496.3L435.4 473.8C423.3 482.9 410.1 490.5 396.1 496.6L381.7 566.5C378.6 581.4 365.5 592 350.4 592L290.6 592C275.4 592 262.3 581.3 259.3 566.5L244.9 496.6C230.8 490.6 217.7 482.9 205.6 473.8L137.5 496.3C123.1 501.1 107.3 495.1 99.7 481.9L69.8 430.1C62.2 416.9 64.9 400.3 76.3 390.2L129.7 342.7C128.8 335.3 128.4 327.7 128.4 320C128.4 312.3 128.9 304.7 129.7 297.3L76.3 249.8C64.9 239.7 62.3 223 69.8 209.9L99.7 158.1C107.3 144.9 123.1 138.9 137.5 143.7L205.3 166.2C217.4 157.1 230.6 149.5 244.6 143.4L259.1 73.5zM320.3 400C364.5 399.8 400.2 363.9 400 319.7C399.8 275.5 363.9 239.8 319.7 240C275.5 240.2 239.8 276.1 240 320.3C240.2 364.5 276.1 400.2 320.3 400z"/></svg>',"grip-vertical":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M128 40c0-22.1-17.9-40-40-40L40 0C17.9 0 0 17.9 0 40L0 88c0 22.1 17.9 40 40 40l48 0c22.1 0 40-17.9 40-40l0-48zm0 192c0-22.1-17.9-40-40-40l-48 0c-22.1 0-40 17.9-40 40l0 48c0 22.1 17.9 40 40 40l48 0c22.1 0 40-17.9 40-40l0-48zM0 424l0 48c0 22.1 17.9 40 40 40l48 0c22.1 0 40-17.9 40-40l0-48c0-22.1-17.9-40-40-40l-48 0c-22.1 0-40 17.9-40 40zM320 40c0-22.1-17.9-40-40-40L232 0c-22.1 0-40 17.9-40 40l0 48c0 22.1 17.9 40 40 40l48 0c22.1 0 40-17.9 40-40l0-48zM192 232l0 48c0 22.1 17.9 40 40 40l48 0c22.1 0 40-17.9 40-40l0-48c0-22.1-17.9-40-40-40l-48 0c-22.1 0-40 17.9-40 40zM320 424c0-22.1-17.9-40-40-40l-48 0c-22.1 0-40 17.9-40 40l0 48c0 22.1 17.9 40 40 40l48 0c22.1 0 40-17.9 40-40l0-48z"/></svg>',indeterminate:'<svg part="indeterminate-icon" class="icon" viewBox="0 0 16 16"><g stroke="none" stroke-width="1" fill="none" fill-rule="evenodd" stroke-linecap="round"><g stroke="currentColor" stroke-width="2"><g transform="translate(2.285714 6.857143)"><path d="M10.2857143,1.14285714 L1.14285714,1.14285714"/></g></g></g></svg>',minus:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M0 256c0-17.7 14.3-32 32-32l384 0c17.7 0 32 14.3 32 32s-14.3 32-32 32L32 288c-17.7 0-32-14.3-32-32z"/></svg>',pause:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 384 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M48 32C21.5 32 0 53.5 0 80L0 432c0 26.5 21.5 48 48 48l64 0c26.5 0 48-21.5 48-48l0-352c0-26.5-21.5-48-48-48L48 32zm224 0c-26.5 0-48 21.5-48 48l0 352c0 26.5 21.5 48 48 48l64 0c26.5 0 48-21.5 48-48l0-352c0-26.5-21.5-48-48-48l-64 0z"/></svg>',"picture-in-picture":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M448 32c35.3 0 64 28.7 64 64l0 112-64 0 0-112-384 0 0 320 144 0 0 64-144 0-6.5-.3c-30.1-3.1-54.1-27-57.1-57.1L0 416 0 96C0 62.9 25.2 35.6 57.5 32.3L64 32 448 32zm16 224c26.5 0 48 21.5 48 48l0 128c0 26.5-21.5 48-48 48l-160 0c-26.5 0-48-21.5-48-48l0-128c0-26.5 21.5-48 48-48l160 0z"/></svg>',play:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M91.2 36.9c-12.4-6.8-27.4-6.5-39.6 .7S32 57.9 32 72l0 368c0 14.1 7.5 27.2 19.6 34.4s27.2 7.5 39.6 .7l336-184c12.8-7 20.8-20.5 20.8-35.1s-8-28.1-20.8-35.1l-336-184z"/></svg>',"play-circle":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M0 256a256 256 0 1 1 512 0 256 256 0 1 1 -512 0zM188.3 147.1c-7.6 4.2-12.3 12.3-12.3 20.9l0 176c0 8.7 4.7 16.7 12.3 20.9s16.8 4.1 24.3-.5l144-88c7.1-4.4 11.5-12.1 11.5-20.5s-4.4-16.1-11.5-20.5l-144-88c-7.4-4.5-16.7-4.7-24.3-.5z"/></svg>',plus:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free 7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M352 128C352 110.3 337.7 96 320 96C302.3 96 288 110.3 288 128L288 288L128 288C110.3 288 96 302.3 96 320C96 337.7 110.3 352 128 352L288 352L288 512C288 529.7 302.3 544 320 544C337.7 544 352 529.7 352 512L352 352L512 352C529.7 352 544 337.7 544 320C544 302.3 529.7 288 512 288L352 288L352 128z"/></svg>',star:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 576 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M309.5-18.9c-4.1-8-12.4-13.1-21.4-13.1s-17.3 5.1-21.4 13.1L193.1 125.3 33.2 150.7c-8.9 1.4-16.3 7.7-19.1 16.3s-.5 18 5.8 24.4l114.4 114.5-25.2 159.9c-1.4 8.9 2.3 17.9 9.6 23.2s16.9 6.1 25 2L288.1 417.6 432.4 491c8 4.1 17.7 3.3 25-2s11-14.2 9.6-23.2L441.7 305.9 556.1 191.4c6.4-6.4 8.6-15.8 5.8-24.4s-10.1-14.9-19.1-16.3L383 125.3 309.5-18.9z"/></svg>',upload:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free 7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M352 173.3L352 384C352 401.7 337.7 416 320 416C302.3 416 288 401.7 288 384L288 173.3L246.6 214.7C234.1 227.2 213.8 227.2 201.3 214.7C188.8 202.2 188.8 181.9 201.3 169.4L297.3 73.4C309.8 60.9 330.1 60.9 342.6 73.4L438.6 169.4C451.1 181.9 451.1 202.2 438.6 214.7C426.1 227.2 405.8 227.2 393.3 214.7L352 173.3zM320 464C364.2 464 400 428.2 400 384L480 384C515.3 384 544 412.7 544 448L544 480C544 515.3 515.3 544 480 544L160 544C124.7 544 96 515.3 96 480L96 448C96 412.7 124.7 384 160 384L240 384C240 428.2 275.8 464 320 464zM464 488C477.3 488 488 477.3 488 464C488 450.7 477.3 440 464 440C450.7 440 440 450.7 440 464C440 477.3 450.7 488 464 488z"/></svg>',user:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M224 248a120 120 0 1 0 0-240 120 120 0 1 0 0 240zm-29.7 56C95.8 304 16 383.8 16 482.3 16 498.7 29.3 512 45.7 512l356.6 0c16.4 0 29.7-13.3 29.7-29.7 0-98.5-79.8-178.3-178.3-178.3l-59.4 0z"/></svg>',volume:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M48 352l48 0 134.1 119.2c6.4 5.7 14.6 8.8 23.1 8.8 19.2 0 34.8-15.6 34.8-34.8l0-378.4c0-19.2-15.6-34.8-34.8-34.8-8.5 0-16.7 3.1-23.1 8.8L96 160 48 160c-26.5 0-48 21.5-48 48l0 96c0 26.5 21.5 48 48 48zM441.1 107c-10.3-8.4-25.4-6.8-33.8 3.5s-6.8 25.4 3.5 33.8C443.3 170.7 464 210.9 464 256s-20.7 85.3-53.2 111.8c-10.3 8.4-11.8 23.5-3.5 33.8s23.5 11.8 33.8 3.5c43.2-35.2 70.9-88.9 70.9-149s-27.7-113.8-70.9-149zm-60.5 74.5c-10.3-8.4-25.4-6.8-33.8 3.5s-6.8 25.4 3.5 33.8C361.1 227.6 368 241 368 256s-6.9 28.4-17.7 37.3c-10.3 8.4-11.8 23.5-3.5 33.8s23.5 11.8 33.8 3.5C402.1 312.9 416 286.1 416 256s-13.9-56.9-35.5-74.5z"/></svg>',"volume-low":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M48 352l48 0 134.1 119.2c6.4 5.7 14.6 8.8 23.1 8.8 19.2 0 34.8-15.6 34.8-34.8l0-378.4c0-19.2-15.6-34.8-34.8-34.8-8.5 0-16.7 3.1-23.1 8.8L96 160 48 160c-26.5 0-48 21.5-48 48l0 96c0 26.5 21.5 48 48 48zM380.6 181.5c-10.3-8.4-25.4-6.8-33.8 3.5s-6.8 25.4 3.5 33.8C361.1 227.6 368 241 368 256s-6.9 28.4-17.7 37.3c-10.3 8.4-11.8 23.5-3.5 33.8s23.5 11.8 33.8 3.5C402.1 312.9 416 286.1 416 256s-13.9-56.9-35.5-74.5z"/></svg>',"volume-xmark":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 576 512"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path fill="currentColor" d="M48 352l48 0 134.1 119.2c6.4 5.7 14.6 8.8 23.1 8.8 19.2 0 34.8-15.6 34.8-34.8l0-378.4c0-19.2-15.6-34.8-34.8-34.8-8.5 0-16.7 3.1-23.1 8.8L96 160 48 160c-26.5 0-48 21.5-48 48l0 96c0 26.5 21.5 48 48 48zM367 175c-9.4 9.4-9.4 24.6 0 33.9l47 47-47 47c-9.4 9.4-9.4 24.6 0 33.9s24.6 9.4 33.9 0l47-47 47 47c9.4 9.4 24.6 9.4 33.9 0s9.4-24.6 0-33.9l-47-47 47-47c9.4-9.4 9.4-24.6 0-33.9s-24.6-9.4-33.9 0l-47 47-47-47c-9.4-9.4-24.6-9.4-33.9 0z"/></svg>',xmark:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 384 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M55.1 73.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3L147.2 256 9.9 393.4c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0L192.5 301.3 329.9 438.6c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L237.8 256 375.1 118.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L192.5 210.7 55.1 73.4z"/></svg>'},regular:{calendar:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path d="M216 64C229.3 64 240 74.7 240 88L240 128L400 128L400 88C400 74.7 410.7 64 424 64C437.3 64 448 74.7 448 88L448 128L480 128C515.3 128 544 156.7 544 192L544 480C544 515.3 515.3 544 480 544L160 544C124.7 544 96 515.3 96 480L96 192C96 156.7 124.7 128 160 128L192 128L192 88C192 74.7 202.7 64 216 64zM216 176L160 176C151.2 176 144 183.2 144 192L144 240L496 240L496 192C496 183.2 488.8 176 480 176L216 176zM144 288L144 480C144 488.8 151.2 496 160 496L480 496C488.8 496 496 488.8 496 480L496 288L144 288z"/></svg>',"circle-question":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M464 256a208 208 0 1 0 -416 0 208 208 0 1 0 416 0zM0 256a256 256 0 1 1 512 0 256 256 0 1 1 -512 0zm256-80c-17.7 0-32 14.3-32 32 0 13.3-10.7 24-24 24s-24-10.7-24-24c0-44.2 35.8-80 80-80s80 35.8 80 80c0 47.2-36 67.2-56 74.5l0 3.8c0 13.3-10.7 24-24 24s-24-10.7-24-24l0-8.1c0-20.5 14.8-35.2 30.1-40.2 6.4-2.1 13.2-5.5 18.2-10.3 4.3-4.2 7.7-10 7.7-19.6 0-17.7-14.3-32-32-32zM224 368a32 32 0 1 1 64 0 32 32 0 1 1 -64 0z"/></svg>',"circle-xmark":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M256 48a208 208 0 1 1 0 416 208 208 0 1 1 0-416zm0 464a256 256 0 1 0 0-512 256 256 0 1 0 0 512zM167 167c-9.4 9.4-9.4 24.6 0 33.9l55 55-55 55c-9.4 9.4-9.4 24.6 0 33.9s24.6 9.4 33.9 0l55-55 55 55c9.4 9.4 24.6 9.4 33.9 0s9.4-24.6 0-33.9l-55-55 55-55c9.4-9.4 9.4-24.6 0-33.9s-24.6-9.4-33.9 0l-55 55-55-55c-9.4-9.4-24.6-9.4-33.9 0z"/></svg>',clock:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.2.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2026 Fonticons, Inc.--><path d="M528 320C528 434.9 434.9 528 320 528C205.1 528 112 434.9 112 320C112 205.1 205.1 112 320 112C434.9 112 528 205.1 528 320zM64 320C64 461.4 178.6 576 320 576C461.4 576 576 461.4 576 320C576 178.6 461.4 64 320 64C178.6 64 64 178.6 64 320zM296 184L296 320C296 328 300 335.5 306.7 340L402.7 404C413.7 411.4 428.6 408.4 436 397.3C443.4 386.2 440.4 371.4 429.3 364L344 307.2L344 184C344 170.7 333.3 160 320 160C306.7 160 296 170.7 296 184z"/></svg>',copy:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 448 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M384 336l-192 0c-8.8 0-16-7.2-16-16l0-256c0-8.8 7.2-16 16-16l133.5 0c4.2 0 8.3 1.7 11.3 4.7l58.5 58.5c3 3 4.7 7.1 4.7 11.3L400 320c0 8.8-7.2 16-16 16zM192 384l192 0c35.3 0 64-28.7 64-64l0-197.5c0-17-6.7-33.3-18.7-45.3L370.7 18.7C358.7 6.7 342.5 0 325.5 0L192 0c-35.3 0-64 28.7-64 64l0 256c0 35.3 28.7 64 64 64zM64 128c-35.3 0-64 28.7-64 64L0 448c0 35.3 28.7 64 64 64l192 0c35.3 0 64-28.7 64-64l0-16-48 0 0 16c0 8.8-7.2 16-16 16L64 464c-8.8 0-16-7.2-16-16l0-256c0-8.8 7.2-16 16-16l16 0 0-48-16 0z"/></svg>',eye:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 576 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M288 80C222.8 80 169.2 109.6 128.1 147.7 89.6 183.5 63 226 49.4 256 63 286 89.6 328.5 128.1 364.3 169.2 402.4 222.8 432 288 432s118.8-29.6 159.9-67.7C486.4 328.5 513 286 526.6 256 513 226 486.4 183.5 447.9 147.7 406.8 109.6 353.2 80 288 80zM95.4 112.6C142.5 68.8 207.2 32 288 32s145.5 36.8 192.6 80.6c46.8 43.5 78.1 95.4 93 131.1 3.3 7.9 3.3 16.7 0 24.6-14.9 35.7-46.2 87.7-93 131.1-47.1 43.7-111.8 80.6-192.6 80.6S142.5 443.2 95.4 399.4c-46.8-43.5-78.1-95.4-93-131.1-3.3-7.9-3.3-16.7 0-24.6 14.9-35.7 46.2-87.7 93-131.1zM288 336c44.2 0 80-35.8 80-80 0-29.6-16.1-55.5-40-69.3-1.4 59.7-49.6 107.9-109.3 109.3 13.8 23.9 39.7 40 69.3 40zm-79.6-88.4c2.5 .3 5 .4 7.6 .4 35.3 0 64-28.7 64-64 0-2.6-.2-5.1-.4-7.6-37.4 3.9-67.2 33.7-71.1 71.1zm45.6-115c10.8-3 22.2-4.5 33.9-4.5 8.8 0 17.5 .9 25.8 2.6 .3 .1 .5 .1 .8 .2 57.9 12.2 101.4 63.7 101.4 125.2 0 70.7-57.3 128-128 128-61.6 0-113-43.5-125.2-101.4-1.8-8.6-2.8-17.5-2.8-26.6 0-11 1.4-21.8 4-32 .2-.7 .3-1.3 .5-1.9 11.9-43.4 46.1-77.6 89.5-89.5z"/></svg>',"eye-slash":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 576 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M41-24.9c-9.4-9.4-24.6-9.4-33.9 0S-2.3-.3 7 9.1l528 528c9.4 9.4 24.6 9.4 33.9 0s9.4-24.6 0-33.9l-96.4-96.4c2.7-2.4 5.4-4.8 8-7.2 46.8-43.5 78.1-95.4 93-131.1 3.3-7.9 3.3-16.7 0-24.6-14.9-35.7-46.2-87.7-93-131.1-47.1-43.7-111.8-80.6-192.6-80.6-56.8 0-105.6 18.2-146 44.2L41-24.9zM176.9 111.1c32.1-18.9 69.2-31.1 111.1-31.1 65.2 0 118.8 29.6 159.9 67.7 38.5 35.7 65.1 78.3 78.6 108.3-13.6 30-40.2 72.5-78.6 108.3-3.1 2.8-6.2 5.6-9.4 8.4L393.8 328c14-20.5 22.2-45.3 22.2-72 0-70.7-57.3-128-128-128-26.7 0-51.5 8.2-72 22.2l-39.1-39.1zm182 182l-108-108c11.1-5.8 23.7-9.1 37.1-9.1 44.2 0 80 35.8 80 80 0 13.4-3.3 26-9.1 37.1zM103.4 173.2l-34-34c-32.6 36.8-55 75.8-66.9 104.5-3.3 7.9-3.3 16.7 0 24.6 14.9 35.7 46.2 87.7 93 131.1 47.1 43.7 111.8 80.6 192.6 80.6 37.3 0 71.2-7.9 101.5-20.6L352.2 422c-20 6.4-41.4 10-64.2 10-65.2 0-118.8-29.6-159.9-67.7-38.5-35.7-65.1-78.3-78.6-108.3 10.4-23.1 28.6-53.6 54-82.8z"/></svg>',star:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 576 512"><!--! Font Awesome Free 7.0.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc. --><path fill="currentColor" d="M288.1-32c9 0 17.3 5.1 21.4 13.1L383 125.3 542.9 150.7c8.9 1.4 16.3 7.7 19.1 16.3s.5 18-5.8 24.4L441.7 305.9 467 465.8c1.4 8.9-2.3 17.9-9.6 23.2s-17 6.1-25 2L288.1 417.6 143.8 491c-8 4.1-17.7 3.3-25-2s-11-14.2-9.6-23.2L134.4 305.9 20 191.4c-6.4-6.4-8.6-15.8-5.8-24.4s10.1-14.9 19.1-16.3l159.9-25.4 73.6-144.2c4.1-8 12.4-13.1 21.4-13.1zm0 76.8L230.3 158c-3.5 6.8-10 11.6-17.6 12.8l-125.5 20 89.8 89.9c5.4 5.4 7.9 13.1 6.7 20.7l-19.8 125.5 113.3-57.6c6.8-3.5 14.9-3.5 21.8 0l113.3 57.6-19.8-125.5c-1.2-7.6 1.3-15.3 6.7-20.7l89.8-89.9-125.5-20c-7.6-1.2-14.1-6-17.6-12.8L288.1 44.8z"/></svg>'}},Mi={name:"system",resolver:(e,t="classic",i="solid")=>{let n=jt[i][e]??jt.regular[e]??jt.regular["circle-question"];return n?ki(n):""}},Qe=Mi;/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var Fi="classic",Ii=[Je,Qe],ti=new Set;function ei(e){ti.add(e)}function ii(e){ti.delete(e)}function At(e){return Ii.find(t=>t.name===e)}function oi(){return Fi}/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */var lt=Symbol(),zt=Symbol(),Ht,Wt=new Map,b=class extends x{constructor(){super(...arguments),this.svg=null,this.autoWidth=!1,this.swapOpacity=!1,this.label="",this.library="default",this.rotate=0,this.resolveIcon=async(e,t)=>{let i;if(t?.spriteSheet){this.hasUpdated||await this.updateComplete,this.svg=y`<svg part="svg">
        <use part="use" href="${e}"></use>
      </svg>`,await this.updateComplete;let o=this.shadowRoot.querySelector("[part='svg']");return typeof t.mutator=="function"&&t.mutator(o,this),this.svg}try{if(i=await fetch(e,{mode:"cors"}),!i.ok)return i.status===410?lt:zt}catch{return zt}try{let o=document.createElement("div");o.innerHTML=await i.text();let n=o.firstElementChild;if(n?.tagName?.toLowerCase()!=="svg")return lt;Ht||(Ht=new DOMParser);let a=Ht.parseFromString(n.outerHTML,"text/html").body.querySelector("svg");return a?(a.part.add("svg"),document.adoptNode(a)):lt}catch{return lt}}}connectedCallback(){super.connectedCallback(),ei(this)}firstUpdated(e){super.firstUpdated(e),this.hasAttribute("rotate")&&this.style.setProperty("--rotate-angle",`${this.rotate}deg`),this.setIcon()}disconnectedCallback(){super.disconnectedCallback(),ii(this)}async getIconSource(){let e=At(this.library),t=this.family||oi();if(this.name&&e){let i=this.canvas==="auto"||this.autoWidth,o;try{o=await e.resolver(this.name,t,this.variant,i)}catch{o=void 0}return{url:o,fromLibrary:!0}}return{url:this.src,fromLibrary:!1}}handleLabelChange(){typeof this.label=="string"&&this.label.length>0?(this.setAttribute("role","img"),this.setAttribute("aria-label",this.label),this.removeAttribute("aria-hidden")):(this.removeAttribute("role"),this.removeAttribute("aria-label"),this.setAttribute("aria-hidden","true"))}async setIcon(){let{url:e,fromLibrary:t}=await this.getIconSource(),i=t?At(this.library):void 0;if(!e){this.svg=null;return}let o=Wt.get(e);o||(o=this.resolveIcon(e,i),Wt.set(e,o));let n=await o;n===zt&&Wt.delete(e);let s=await this.getIconSource();if(e===s.url){if(Se(n)){this.svg=n;return}switch(n){case zt:case lt:this.svg=null,this.dispatchEvent(new We);break;default:this.svg=n.cloneNode(!0),i?.mutator?.(this.svg,this),this.dispatchEvent(new Xe)}}}willUpdate(e){return this.style||this.setStyleProperty("--rotate-angle",`${this.rotate}deg`),super.willUpdate(e)}updated(e){super.updated(e);let t=At(this.library);this.hasAttribute("rotate")&&this.style.setProperty("--rotate-angle",`${this.rotate}deg`);let i=this.shadowRoot?.querySelector("svg");i&&t?.mutator?.(i,this)}render(){return this.hasUpdated?this.svg:y`<svg part="svg" width="16" height="16" viewBox="0 0 16 16"></svg>`}};b.css=Ye;r([V()],b.prototype,"svg",2);r([d({reflect:!0})],b.prototype,"name",2);r([d({reflect:!0})],b.prototype,"family",2);r([d({reflect:!0})],b.prototype,"variant",2);r([d({reflect:!0})],b.prototype,"canvas",2);r([d({attribute:"auto-width",type:Boolean,reflect:!0})],b.prototype,"autoWidth",2);r([d({attribute:"swap-opacity",type:Boolean,reflect:!0})],b.prototype,"swapOpacity",2);r([d()],b.prototype,"src",2);r([d()],b.prototype,"label",2);r([d({reflect:!0})],b.prototype,"library",2);r([d({type:Number,reflect:!0})],b.prototype,"rotate",2);r([d({type:String,reflect:!0})],b.prototype,"flip",2);r([d({type:String,reflect:!0})],b.prototype,"animation",2);r([g("label")],b.prototype,"handleLabelChange",1);r([g(["family","name","library","variant","src","autoWidth","canvas","swapOpacity"],{waitUntilFirstUpdate:!0})],b.prototype,"setIcon",1);b=r([z("wa-icon")],b);/*! Copyright 2026 Fonticons, Inc. - https://webawesome.com/license */export{A as default};
