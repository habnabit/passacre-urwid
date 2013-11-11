import purwid


def test_merge_sorted_list():
    src = range(6)
    dst = [1, 3, 4]
    purwid.merge_sorted_lists(src, dst)
    assert src == dst

def test_merge_sorted_list_with_empty_list():
    src = range(6)
    dst = []
    purwid.merge_sorted_lists(src, dst)
    assert src == dst
