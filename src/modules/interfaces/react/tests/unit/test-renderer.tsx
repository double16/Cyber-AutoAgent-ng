import React, {ReactElement} from 'react';
import {act} from 'react';
import {createRoot, Root} from 'react-dom/client';

type HostType = keyof HTMLElementTagNameMap | string;

export interface ReactTestRendererJSON {
    type: string;
    props: Record<string, unknown>;
    children: Array<ReactTestRendererJSON | string> | null;
}

export class TestInstance {
    constructor(private readonly element: Element) {
    }

    get instance() {
        return null;
    }

    get props() {
        const element = this.element as HTMLInputElement | HTMLSelectElement | HTMLButtonElement;
        const value =
            this.element.tagName.toLowerCase() === 'input' && element.type === 'number' && element.value !== ''
                ? Number(element.value)
                : element.value;
        const props: Record<string, unknown> = {
            children: element.textContent || undefined,
            id: element.id || undefined,
            type: (element as HTMLInputElement).type || undefined,
            value,
            checked: (element as HTMLInputElement).checked,
            onClick: () => {
                element.dispatchEvent(new MouseEvent('click', {bubbles: true}));
            },
            onChange: (event: { target?: { value?: unknown; checked?: unknown } } = {}) => {
                const target = event.target || {};
                if (this.element.tagName.toLowerCase() === 'input' && ['checkbox', 'radio'].includes(element.type)) {
                    if ('checked' in target && element.checked !== Boolean(target.checked)) {
                        element.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                    }
                    return;
                }
                if ('value' in target) {
                    setNativeValue(element, target.value);
                }
                if ('checked' in target && 'checked' in element) {
                    (element as HTMLInputElement).checked = Boolean(target.checked);
                }
                element.dispatchEvent(new Event('input', {bubbles: true}));
                element.dispatchEvent(new Event('change', {bubbles: true}));
            },
        };

        for (const attribute of Array.from(element.attributes)) {
            if (props[attribute.name] === undefined) {
                props[attribute.name] = attribute.value;
            }
        }

        return props;
    }

    findAllByType(type: HostType): TestInstance[] {
        return findAllByType(this.element, type);
    }

    findByType(type: HostType): TestInstance {
        const matches = this.findAllByType(type);
        if (matches.length !== 1) {
            throw new Error(`Expected one ${type} element, found ${matches.length}`);
        }
        return matches[0];
    }
}

export class ReactTestRenderer {
    readonly root: TestInstance;

    constructor(
        private readonly reactRoot: Root,
        private readonly container: HTMLDivElement,
        element: ReactElement,
    ) {
        this.root = new TestInstance(container);
        this.reactRoot.render(element);
    }

    update(element: ReactElement) {
        this.reactRoot.render(element);
    }

    unmount() {
        this.reactRoot.unmount();
        this.container.remove();
    }

    toJSON(): ReactTestRendererJSON | ReactTestRendererJSON[] | string | null {
        const nodes = Array.from(this.container.childNodes)
            .map(nodeToJSON)
            .filter((node): node is ReactTestRendererJSON | string => node !== null);

        if (nodes.length === 0) {
            return null;
        }
        return nodes.length === 1 ? nodes[0] : nodes;
    }
}

const create = (element: ReactElement): ReactTestRenderer => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    return new ReactTestRenderer(createRoot(container), container, element);
};

const findAllByType = (container: Element, type: HostType): TestInstance[] =>
    Array.from(container.querySelectorAll(String(type))).map(element => new TestInstance(element));

const nodeToJSON = (node: ChildNode): ReactTestRendererJSON | string | null => {
    if (node.nodeType === Node.TEXT_NODE) {
        return node.textContent || null;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) {
        return null;
    }

    const element = node as Element;
    const props: Record<string, unknown> = {};
    for (const attribute of Array.from(element.attributes)) {
        props[attribute.name] = attribute.value;
    }

    const children = Array.from(element.childNodes)
        .map(nodeToJSON)
        .filter((child): child is ReactTestRendererJSON | string => child !== null);

    return {
        type: element.tagName.toLowerCase(),
        props,
        children: children.length > 0 ? children : null,
    };
};

const setNativeValue = (element: Element, value: unknown) => {
    const htmlElement = element as HTMLInputElement | HTMLSelectElement;
    const prototype = Object.getPrototypeOf(htmlElement);
    const valueSetter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
    if (valueSetter) {
        valueSetter.call(htmlElement, value);
    } else {
        htmlElement.setAttribute('value', String(value));
    }
};

export {act};

export default {
    create,
};
