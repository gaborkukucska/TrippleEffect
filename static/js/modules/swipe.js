// static/js/modules/swipe.js (v8 - Debug Overshoot - Final Checks)

let contentWrapper = null;
let swipeSections = null;
let numSections = 0;
let touchStartX = 0;
let touchStartY = 0;
let touchCurrentX = 0;
let isSwiping = false;
let horizontalSwipeConfirmed = false;
const swipeThreshold = 50;
let getCurrentSectionIndexCallback = () => 0;
let setCurrentSectionIndexCallback = (index) => {};

export function initSwipe(wrapper, sections, getIndexFunc, setIndexFunc) {
    contentWrapper = wrapper;
    swipeSections = sections;
    numSections = sections.length;
    getCurrentSectionIndexCallback = getIndexFunc;
    setCurrentSectionIndexCallback = setIndexFunc;
    console.log(`Swipe module initialized with ${numSections} sections.`);
    updateContentWrapperTransform(getCurrentSectionIndexCallback(), false);
}

export function handleTouchStart(event) {
    if (!contentWrapper) return;
    const target = event.target;
    const isInteractive = target.closest('button, input, textarea, select, a, .modal-content');
    const isInsideScrollable = target.closest('.message-area, #agent-status-content, #config-content');
    const isModalOpen = document.querySelector('.modal[style*="display: block"]');
    if (isInteractive || isModalOpen || isInsideScrollable) {
        isSwiping = false; console.log(`Swipe ignored (target: ${target.tagName}, interact: ${!!isInteractive}, insideScroll: ${!!isInsideScrollable}, modal: ${!!isModalOpen})`); return;
    }
    try {
        touchStartX = event.touches[0].clientX; touchStartY = event.touches[0].clientY; touchCurrentX = touchStartX;
        isSwiping = true; horizontalSwipeConfirmed = false;
        contentWrapper.style.transition = 'none'; console.log(`TouchStart: startX=${touchStartX.toFixed(0)}`);
    } catch (e) { console.error("Error in touchstart:", e); isSwiping = false; }
}

export function handleTouchMove(event) {
    if (!isSwiping || !contentWrapper) return;
    try {
        const currentY = event.touches[0].clientY; touchCurrentX = event.touches[0].clientX;
        const diffX = touchCurrentX - touchStartX; const diffY = currentY - touchStartY;
        if (!horizontalSwipeConfirmed) {
            if (Math.abs(diffX) > Math.abs(diffY) + 5) { horizontalSwipeConfirmed = true; console.log("Horizontal swipe confirmed."); }
            else if (Math.abs(diffY) > Math.abs(diffX) + 5) { isSwiping = false; console.log("Vertical scroll detected, canceling swipe."); return; }
        }
        if (horizontalSwipeConfirmed) {
            event.preventDefault(); const currentIdx = getCurrentSectionIndexCallback(); const baseTranslateXPercent = -currentIdx * 100;
            contentWrapper.style.transform = `translateX(calc(${baseTranslateXPercent}% + ${diffX}px))`;
        }
    } catch (e) { console.error("Error in touchmove:", e); isSwiping = false; }
}

export function handleTouchEnd(event) {
    if (!isSwiping || !contentWrapper) { isSwiping = false; horizontalSwipeConfirmed = false; return; }
    const wasHorizontal = horizontalSwipeConfirmed;
    isSwiping = false; horizontalSwipeConfirmed = false;

    let currentIdx = getCurrentSectionIndexCallback();
    let targetIndex = currentIdx; // Default to snap back

    if (wasHorizontal) {
        const diffX = touchCurrentX - touchStartX;
        console.log(`TouchEnd (H): Index BEFORE=${currentIdx}, diffX=${diffX.toFixed(0)}, thres=${swipeThreshold}`);
        if (Math.abs(diffX) > swipeThreshold) {
            if (diffX < 0) { targetIndex = currentIdx + 1; console.log(`Swipe Left -> Proposed Index: ${targetIndex}`); }
            else { targetIndex = currentIdx - 1; console.log(`Swipe Right -> Proposed Index: ${targetIndex}`); }
            // Clamp the target index immediately after proposing
            targetIndex = Math.max(0, Math.min(targetIndex, numSections - 1));
            console.log(`Target Index Clamped: ${targetIndex}`);
        } else { console.log("Swipe distance below threshold. Target Index remains:", targetIndex); }
    } else { console.log("TouchEnd: No horizontal swipe confirmed. Target Index remains:", targetIndex); }

    console.log(`TouchEnd: Final Target Index determined: ${targetIndex}`);
    // Update state in app.js state ONLY IF the index actually changed
    if (targetIndex !== currentIdx) {
        console.log(`TouchEnd: Index changed from ${currentIdx} to ${targetIndex}. Calling setIndex(${targetIndex})`);
        setCurrentSectionIndexCallback(targetIndex);
    } else {
        console.log(`TouchEnd: Index did not change (${targetIndex}). Not calling setIndex.`);
    }

    // Apply the final transform WITH transition, using the definitively clamped index
    updateContentWrapperTransform(targetIndex, true);
}


export function updateContentWrapperTransform(index, useTransition = true) {
    // Log the index received by this specific function call
    console.log(`updateContentWrapperTransform CALLED with index=${index}, useTransition=${useTransition}`);

    // Clamp index AGAIN here just before applying transform, as the ultimate safety check
    const clampedIndex = Math.max(0, Math.min(numSections - 1, index));
    if (clampedIndex !== index) {
         console.warn(`Index ${index} was clamped to ${clampedIndex} just before applying transform!`);
    }

    if (contentWrapper && typeof clampedIndex === 'number') {
        const newTranslateXPercent = -clampedIndex * 100;
        // Log the final values being applied
        console.log(`---> Applying transform NOW: Target Index=${clampedIndex}, TranslateX=${newTranslateXPercent}% (transition: ${useTransition})`);

        // Apply transform FIRST
        contentWrapper.style.transform = `translateX(${newTranslateXPercent}%)`;
        // Apply transition AFTER setting the target transform state
        contentWrapper.style.transition = useTransition ? 'transform 0.3s ease-out' : 'none';

        // Log computed style after a short delay (unchanged)
        if (useTransition) {
            setTimeout(() => {
                if (contentWrapper) { // Check if wrapper still exists
                    const currentTransform = window.getComputedStyle(contentWrapper).transform;
                    console.log(`---> Transform AFTER 350ms check: Index=${clampedIndex}, Style=${currentTransform}`);
                }
            }, 350);
        }
    } else {
        console.error(`Invalid call to updateContentWrapperTransform: Index=${index}, Clamped=${clampedIndex}, Wrapper=${!!contentWrapper}`);
    }
}

export function addKeyboardNavListeners(getIndex, setIndex) {
     document.addEventListener('keydown', (e) => {
        const targetTagName = document.activeElement?.tagName.toLowerCase();
        const isModalOpen = document.querySelector('.modal[style*="display: block"]');
        const isInputFocused = ['textarea', 'input', 'select'].includes(targetTagName);
        if (isModalOpen || isInputFocused) return;

        let currentIndex = getIndex();
        let newIndex = currentIndex;

        if (e.key === 'ArrowLeft' && currentIndex > 0) { newIndex = currentIndex - 1; console.log(`Key Left -> New Index: ${newIndex}`); }
        else if (e.key === 'ArrowRight' && currentIndex < numSections - 1) { newIndex = currentIndex + 1; console.log(`Key Right -> New Index: ${newIndex}`); }

        if (newIndex !== currentIndex) { setIndex(newIndex); updateContentWrapperTransform(newIndex, true); }
    });
    console.log("Keyboard navigation listeners added.");
}
