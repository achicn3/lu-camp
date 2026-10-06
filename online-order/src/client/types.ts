// 菜單快照的形狀（店內 backend app/modules/onlineorder/snapshot.py 產生）。
export interface MenuOptionView {
  id: number;
  name: string;
  price_delta: number;
  available: boolean;
  remaining: number | null;
}

export interface OptionGroupView {
  id: number;
  name: string;
  min_select: number;
  max_select: number;
  options: MenuOptionView[];
}

export interface MenuItemView {
  id: number;
  name: string;
  description: string | null;
  category_id: number | null;
  unit_price: number;
  photo: string | null;
  available: boolean;
  remaining: number | null;
  option_groups: OptionGroupView[];
  presentation?: MenuPresentation;
}

export interface MenuPresentation {
  flavor_description: string | null;
  audience_description: string | null;
  is_recommended: boolean;
  is_new: boolean;
  limited_on: string | null;
  show_remaining: boolean;
  low_stock_threshold: number;
  hide_sold_out: boolean;
}

export interface MenuSnapshot {
  version: number;
  published_at: string;
  store_name: string;
  font: string | null;
  categories: { id: number; name: string }[];
  items: MenuItemView[];
}

export interface TableView {
  label: string;
  service_mode: "DINE_IN" | "TAKEOUT";
}
