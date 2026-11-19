import { useState } from "react";

import { productPhotoUrl } from "../api/client";
import { Icon } from "./Icon";

interface ProductPhotoProps {
  readonly productId: string;
  readonly hasPhoto: boolean;
  readonly eager?: boolean;
}

/** La foto sobre su recuadro blanco; sin foto, o si no llega, un hueco que lo dice. */
export function ProductPhoto({ productId, hasPhoto, eager = false }: ProductPhotoProps) {
  const [failed, setFailed] = useState(false);
  if (!hasPhoto || failed) {
    return (
      <span className="photo photo-missing">
        <Icon name="photo" size={28} />
        <span>Sin foto</span>
      </span>
    );
  }
  return (
    <span className="photo">
      <img
        src={productPhotoUrl(productId)}
        alt=""
        loading={eager ? "eager" : "lazy"}
        decoding="async"
        onError={() => setFailed(true)}
      />
    </span>
  );
}
