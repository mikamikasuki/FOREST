import type { paths } from "../../../packages/contracts/api.generated";
import { api } from "./api";

export type {
  components,
  paths,
} from "../../../packages/contracts/api.generated";

type HttpMethod =
  "get" | "post" | "put" | "patch" | "delete" | "head" | "options";
type Operation<P extends keyof paths, M extends keyof paths[P]> = NonNullable<
  paths[P][M]
>;
type Parameter<O, K extends "path" | "query"> = O extends {
  parameters: infer P;
}
  ? K extends keyof P
    ? NonNullable<P[K]>
    : never
  : never;
type SuccessResponse<O> = O extends { responses: infer R }
  ? {
      [K in keyof R]: `${K & (string | number)}` extends `2${string}`
        ? K extends 204 | "204"
          ? undefined
          : R[K] extends { content: { "application/json": infer T } }
            ? T
            : never
        : never;
    }[keyof R]
  : never;
type BodyValue<B> = B extends { content: { "application/json": infer T } }
  ? T
  : B extends { content: { "multipart/form-data": unknown } }
    ? FormData
    : never;
type BodyOptions<O> = O extends { requestBody: infer B }
  ? { body: BodyValue<B> }
  : O extends { requestBody?: infer B }
    ? [NonNullable<B>] extends [never]
      ? { body?: never }
      : { body?: BodyValue<NonNullable<B>> }
    : { body?: never };
type ParameterOptions<T, K extends string> = [T] extends [never]
  ? { [P in K]?: never }
  : {} extends T
    ? { [P in K]?: T }
    : { [P in K]: T };
export type ApiRequestOptions<O> = ParameterOptions<
  Parameter<O, "path">,
  "path"
> &
  ParameterOptions<Parameter<O, "query">, "query"> &
  BodyOptions<O>;
export type ApiMethod<P extends keyof paths> = {
  [M in keyof paths[P] & HttpMethod]: [Operation<P, M>] extends [never]
    ? never
    : [SuccessResponse<Operation<P, M>>] extends [never]
      ? never
      : M;
}[keyof paths[P] & HttpMethod];
export type ApiResponse<
  P extends keyof paths,
  M extends keyof paths[P],
> = SuccessResponse<Operation<P, M>>;

type SerializedOptions = {
  path?: Record<string, string | number>;
  query?: Record<string, unknown>;
  body?: unknown;
};

/** Expand a documented path and serialize query parameters without mutating inputs. */
export function apiUrl(
  template: string,
  options: Omit<SerializedOptions, "body"> = {},
): string {
  const route = template.replace(/\{([^{}]+)\}/g, (_, name: string) => {
    const value = options.path?.[name];
    if (value === undefined || value === null)
      throw new Error(`Missing API path parameter: ${name}`);
    return encodeURIComponent(String(value));
  });
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(options.query || {})) {
    const values = Array.isArray(value) ? value : [value];
    for (const item of values)
      if (item !== undefined && item !== null) query.append(name, String(item));
  }
  const encoded = query.toString();
  return encoded
    ? `${route}${route.includes("?") ? "&" : "?"}${encoded}`
    : route;
}

/** JSON operations use the existing transport, authentication, and error semantics. */
export function requestApi<P extends keyof paths, M extends ApiMethod<P>>(
  route: P,
  method: M,
  ...args: {} extends ApiRequestOptions<Operation<P, M>>
    ? [options?: ApiRequestOptions<Operation<P, M>>]
    : [options: ApiRequestOptions<Operation<P, M>>]
): Promise<ApiResponse<P, M>> {
  const options = (args[0] || {}) as SerializedOptions;
  return api<ApiResponse<P, M>>(
    apiUrl(route, options),
    method.toUpperCase(),
    options.body,
  );
}
