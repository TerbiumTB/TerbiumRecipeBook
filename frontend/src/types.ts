export type Quantity = {kind:'exact';value:number;unit:string}|{kind:'range';min:number;max:number;unit:string}|{kind:'to_taste'|'as_needed'};
export type Ingredient={id:string;name:string;quantity:Quantity;preparation:string|null;group:string|null;optional:boolean};
export type Step={id:string;title:string;instruction:string;ingredientIds:string[];timerSeconds:number|null};
export type Recipe={schemaVersion:1;title:string;servings:number;time:{totalMinutes:number;activeMinutes:number};ingredients:Ingredient[];steps:Step[];userNotes:string};
export type RecordRecipe={id:string;revision:number;content:Recipe;source:string};
export type Draft={id:string;baseRecipeId:string|null;baseRevision:number|null;content:Omit<Recipe,'userNotes'>};
export type Reply={assistantMessageId:string;message:string;ideas:{id:string;title:string;description:string}[];recipeDraft:Draft|null};
