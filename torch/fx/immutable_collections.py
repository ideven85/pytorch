from typing import Any, Dict, Iterable, List, Optional, Tuple, Type, TypeVar

from torch.utils._pytree import (
    _dict_flatten,
    _dict_flatten_with_keys,
    _dict_unflatten,
    _list_flatten,
    _list_flatten_with_keys,
    _list_unflatten,
    Context,
    register_pytree_node,
)

from ._compatibility import compatibility


__all__ = ["immutable_list", "immutable_dict"]


_T = TypeVar("_T")


_help_mutation = """\
If you are attempting to modify the kwargs or args of a torch.fx.Node object,
instead create a new copy of it and assign the copy to the node:
    new_args = ... # copy and mutate args
    node.args = new_args
"""


def _no_mutation(self, *args, **kwargs):
    raise NotImplementedError(
        f"'{type(self).__name__}' object does not support mutation. {_help_mutation}",
    )


def _create_immutable_container_class(
    base: Type[_T],
    mutable_functions: Iterable[str],
    namespace: Optional[Dict[str, Any]] = None,
) -> Type[_T]:
    namespace = namespace or {}
    namespace.update((method, _no_mutation) for method in mutable_functions)
    container_class = type("immutable_" + base.__name__, (base,), namespace)
    return container_class


immutable_list = _create_immutable_container_class(
    list,
    (
        "__delitem__",
        "__iadd__",
        "__imul__",
        "__setitem__",
        "append",
        "clear",
        "extend",
        "insert",
        "pop",
        "remove",
        "reverse",
        "sort",
    ),
    namespace={
        "__hash__": lambda self: hash(tuple(self)),
        "__reduce__": lambda self: (type(self), (tuple(self),)),
    },
)
immutable_list = compatibility(is_backward_compatible=True)(immutable_list)


immutable_dict = _create_immutable_container_class(
    dict,
    (
        "__delitem__",
        "__ior__",
        "__setitem__",
        "clear",
        "pop",
        "popitem",
        "setdefault",
        "update",
    ),
    namespace={
        "__hash__": lambda self: hash(tuple(self.items())),
        "__reduce__": lambda self: (type(self), (tuple(self.items()),)),
    },
)
immutable_dict = compatibility(is_backward_compatible=True)(immutable_dict)


# Register immutable collections for PyTree operations
def _immutable_list_flatten(d: List[Any]) -> Tuple[List[Any], Context]:
    return _list_flatten(d)


def _immutable_list_unflatten(
    values: Iterable[Any],
    context: Context,
) -> List[Any]:
    return immutable_list(_list_unflatten(values, context))


def _immutable_dict_flatten(d: Dict[Any, Any]) -> Tuple[List[Any], Context]:
    return _dict_flatten(d)


def _immutable_dict_unflatten(
    values: Iterable[Any],
    context: Context,
) -> Dict[Any, Any]:
    return immutable_dict(_dict_unflatten(values, context))


register_pytree_node(
    immutable_list,
    _immutable_list_flatten,
    _immutable_list_unflatten,
    serialized_type_name="torch.fx.immutable_collections.immutable_list",
    flatten_with_keys_fn=_list_flatten_with_keys,
)
register_pytree_node(
    immutable_dict,
    _immutable_dict_flatten,
    _immutable_dict_unflatten,
    serialized_type_name="torch.fx.immutable_collections.immutable_dict",
    flatten_with_keys_fn=_dict_flatten_with_keys,
)
